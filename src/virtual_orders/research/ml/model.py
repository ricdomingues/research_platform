"""A deterministic gradient-boosted tree ensemble, on NumPy only (Plan 5, D85).

Why not a library: the platform pins every version it depends on and reproduces results years later. A tree
ensemble small enough to read is a better fit for that promise than a large dependency, and this one is under
two hundred lines, trains a few thousand rows in under a second on a laptop, and serializes to a JSON document
a person can inspect. If a future model genuinely needs more, the registry already stores the framework name
beside the artifact, so adding one later does not invalidate what came before.

The algorithm is second-order (Newton) boosting with logistic loss, the same shape XGBoost uses:

    leaf value = -sum(g) / (sum(h) + lambda)
    split gain = 0.5 * [ GL^2/(HL+lambda) + GR^2/(HR+lambda) - G^2/(H+lambda) ] - gamma

with `g = p - y` and `h = p(1 - p)`. There is **no** subsampling, no shuffling and no random initialisation,
so training is a pure function of (rows, labels, hyperparameters): the same inputs always give byte-identical
trees. Candidate thresholds are the midpoints of at most `max_bins` quantiles of each column's finite values,
which bounds the cost without introducing a random element.

Missing values are first class: each split stores which way NaN goes, chosen by whichever side yields the
higher gain during training, so nothing has to be imputed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Any

import numpy as np

from core.domain.hashing import sha256_hex

MODEL_KIND = "GBDT_LOGISTIC_V1"
MODEL_FRAMEWORK = "numpy"


def _as_text(value: float) -> str:
    """Floats are stored as their exact repr: canonical JSON forbids floats, and a rounded model is a different
    model. `float(repr(x)) == x` round-trips for every finite double."""
    return repr(float(value))


@dataclass(frozen=True)
class Hyperparameters:
    n_estimators: int = 60
    learning_rate: float = 0.1
    max_depth: int = 3
    min_samples_leaf: int = 10
    l2_regularization: float = 1.0
    min_split_gain: float = 0.0
    max_bins: int = 32

    def __post_init__(self) -> None:
        if self.n_estimators < 1:
            raise ValueError("n_estimators must be >= 1")
        if not 0 < self.learning_rate <= 1:
            raise ValueError("learning_rate must be in (0, 1]")
        if self.max_depth < 1:
            raise ValueError("max_depth must be >= 1")
        if self.min_samples_leaf < 1:
            raise ValueError("min_samples_leaf must be >= 1")
        if self.l2_regularization < 0 or self.min_split_gain < 0:
            raise ValueError("regularization and min_split_gain must not be negative")
        if self.max_bins < 2:
            raise ValueError("max_bins must be >= 2")

    def as_document(self) -> dict[str, Any]:
        return {item.name: (getattr(self, item.name) if isinstance(getattr(self, item.name), int)
                            else _as_text(getattr(self, item.name))) for item in fields(self)}


@dataclass(frozen=True)
class Node:
    """A split node, or a leaf when `feature` is None."""

    feature: int | None
    threshold: float
    nan_left: bool
    left: int
    right: int
    value: float
    samples: int

    def as_document(self) -> dict[str, Any]:
        return {"feature": self.feature, "threshold": _as_text(self.threshold), "nan_left": self.nan_left,
                "left": self.left, "right": self.right, "value": _as_text(self.value), "samples": self.samples}

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> Node:
        return cls(
            feature=None if document["feature"] is None else int(document["feature"]),
            threshold=float(document["threshold"]), nan_left=bool(document["nan_left"]),
            left=int(document["left"]), right=int(document["right"]), value=float(document["value"]),
            samples=int(document["samples"]),
        )


def _leaf_value(gradient: float, hessian: float, l2: float) -> float:
    return -gradient / (hessian + l2)


def _gain(gradient: float, hessian: float, l2: float) -> float:
    return gradient * gradient / (hessian + l2)


def _thresholds(column: np.ndarray, max_bins: int) -> np.ndarray:
    finite = column[np.isfinite(column)]
    if finite.size == 0:
        return np.empty(0)
    unique = np.unique(finite)
    if unique.size < 2:
        return np.empty(0)
    if unique.size > max_bins:
        positions = np.linspace(0, unique.size - 1, max_bins).astype(int)
        unique = unique[np.unique(positions)]
    midpoints: np.ndarray = (unique[:-1] + unique[1:]) / 2.0
    return midpoints


class Tree:
    """One regression tree over gradients and hessians. Built depth-first, deterministically."""

    def __init__(self, nodes: list[Node]) -> None:
        self.nodes = nodes

    def predict(self, matrix: np.ndarray) -> np.ndarray:
        out = np.zeros(matrix.shape[0], dtype=float)
        for row in range(matrix.shape[0]):
            index = 0
            while self.nodes[index].feature is not None:
                node = self.nodes[index]
                assert node.feature is not None
                value = matrix[row, node.feature]
                if math.isnan(value):
                    index = node.left if node.nan_left else node.right
                else:
                    index = node.left if value <= node.threshold else node.right
            out[row] = self.nodes[index].value
        return out

    def as_document(self) -> list[dict[str, Any]]:
        return [node.as_document() for node in self.nodes]

    @classmethod
    def from_document(cls, document: list[dict[str, Any]]) -> Tree:
        return cls([Node.from_document(item) for item in document])


def _build_tree(
    matrix: np.ndarray, gradient: np.ndarray, hessian: np.ndarray, params: Hyperparameters
) -> Tree:
    nodes: list[Node] = []

    def grow(rows: np.ndarray, depth: int) -> int:
        total_g = float(gradient[rows].sum())
        total_h = float(hessian[rows].sum())
        index = len(nodes)
        nodes.append(Node(None, 0.0, True, -1, -1, _leaf_value(total_g, total_h, params.l2_regularization),
                          int(rows.size)))
        if depth >= params.max_depth or rows.size < 2 * params.min_samples_leaf:
            return index
        parent_gain = _gain(total_g, total_h, params.l2_regularization)
        best: tuple[float, int, float, bool] | None = None
        for feature in range(matrix.shape[1]):
            column = matrix[rows, feature]
            missing = ~np.isfinite(column)
            for threshold in _thresholds(column, params.max_bins):
                goes_left = (column <= threshold) & ~missing
                for nan_left in (True, False):
                    left_mask = goes_left | missing if nan_left else goes_left
                    left_count = int(left_mask.sum())
                    right_count = int(rows.size - left_count)
                    if left_count < params.min_samples_leaf or right_count < params.min_samples_leaf:
                        continue
                    left_g = float(gradient[rows][left_mask].sum())
                    left_h = float(hessian[rows][left_mask].sum())
                    gain = 0.5 * (
                        _gain(left_g, left_h, params.l2_regularization)
                        + _gain(total_g - left_g, total_h - left_h, params.l2_regularization)
                        - parent_gain
                    ) - params.min_split_gain
                    if gain > 0 and (best is None or gain > best[0]):
                        best = (gain, feature, float(threshold), nan_left)
                    if not missing.any():
                        break  # without missing values both NaN directions describe the same split
        if best is None:
            return index
        _, feature, threshold, nan_left = best
        column = matrix[rows, feature]
        missing = ~np.isfinite(column)
        goes_left = (column <= threshold) & ~missing
        left_mask = goes_left | missing if nan_left else goes_left
        left = grow(rows[left_mask], depth + 1)
        right = grow(rows[~left_mask], depth + 1)
        nodes[index] = Node(feature, threshold, nan_left, left, right, nodes[index].value, int(rows.size))
        return index

    grow(np.arange(matrix.shape[0]), 0)
    return Tree(nodes)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    probabilities: np.ndarray = 1.0 / (1.0 + np.exp(-np.clip(values, -60.0, 60.0)))
    return probabilities


@dataclass(frozen=True)
class GbdtModel:
    """A trained ensemble. Serializes to a JSON document with every float written as an exact repr."""

    kind: str
    framework: str
    columns: tuple[str, ...]
    base_score: float
    trees: tuple[Tree, ...]
    hyperparameters: Hyperparameters

    def predict_proba(self, rows: list[tuple[float, ...]] | np.ndarray) -> list[float]:
        """P(label = 1) per row: here, P(target reached before stop)."""
        matrix = np.asarray(rows, dtype=float)
        if matrix.ndim != 2 or matrix.shape[1] != len(self.columns):
            raise ValueError(f"expected {len(self.columns)} columns, got {matrix.shape}")
        scores = np.full(matrix.shape[0], self.base_score, dtype=float)
        for tree in self.trees:
            scores += self.hyperparameters.learning_rate * tree.predict(matrix)
        return [float(value) for value in _sigmoid(scores)]

    def as_document(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "framework": self.framework, "columns": list(self.columns),
            "base_score": _as_text(self.base_score), "hyperparameters": self.hyperparameters.as_document(),
            "trees": [tree.as_document() for tree in self.trees],
        }

    @property
    def artifact_hash(self) -> str:
        """Identity of the trained artifact: every threshold and leaf value, canonically hashed."""
        return sha256_hex(self.as_document())

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> GbdtModel:
        raw = document["hyperparameters"]
        params = Hyperparameters(
            n_estimators=int(raw["n_estimators"]), learning_rate=float(raw["learning_rate"]),
            max_depth=int(raw["max_depth"]), min_samples_leaf=int(raw["min_samples_leaf"]),
            l2_regularization=float(raw["l2_regularization"]), min_split_gain=float(raw["min_split_gain"]),
            max_bins=int(raw["max_bins"]),
        )
        return cls(
            kind=str(document["kind"]), framework=str(document["framework"]),
            columns=tuple(str(name) for name in document["columns"]), base_score=float(document["base_score"]),
            trees=tuple(Tree.from_document(item) for item in document["trees"]), hyperparameters=params,
        )


def fit(
    rows: list[tuple[float, ...]],
    labels: list[int],
    columns: tuple[str, ...],
    *,
    params: Hyperparameters | None = None,
) -> GbdtModel:
    """Train the ensemble. Deterministic: no seed, because nothing here is random."""
    settings = params or Hyperparameters()
    if not rows:
        raise ValueError("training needs at least one row")
    if len(rows) != len(labels):
        raise ValueError("rows and labels must line up")
    if len(set(labels)) < 2:
        raise ValueError("training needs both classes present")
    matrix = np.asarray(rows, dtype=float)
    target = np.asarray(labels, dtype=float)
    positive_rate = float(target.mean())
    base = math.log(positive_rate / (1.0 - positive_rate))
    scores = np.full(matrix.shape[0], base, dtype=float)
    trees: list[Tree] = []
    for _ in range(settings.n_estimators):
        probability = _sigmoid(scores)
        gradient = probability - target
        hessian = np.maximum(probability * (1.0 - probability), 1e-12)
        tree = _build_tree(matrix, gradient, hessian, settings)
        if len(tree.nodes) == 1 and tree.nodes[0].feature is None and abs(tree.nodes[0].value) < 1e-12:
            break  # nothing left to learn: stop rather than pad the ensemble with empty trees
        trees.append(tree)
        scores += settings.learning_rate * tree.predict(matrix)
    return GbdtModel(MODEL_KIND, MODEL_FRAMEWORK, columns, base, tuple(trees), settings)
