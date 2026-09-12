"""Fill model registry. Each version lives in its own frozen module (spec section 0)."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

_VERSIONS = {"v1": "core.fills.v1"}


def get_fill_model(version: str) -> ModuleType:
    if version not in _VERSIONS:
        raise KeyError(f"unknown fill_model_version: {version}")
    return import_module(_VERSIONS[version])
