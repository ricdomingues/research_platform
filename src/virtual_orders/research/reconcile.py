"""What a revisited bucket does to the facts already stored for it (Plan 6, D96).

Pure: given what the engine detects now and what is still active for that bucket, decide which stored facts a
revision superseded and which it retracted. Matching is by pattern, because that is what a reading claims: the
same pattern with a different `evidence_hash` is a corrected reading of the same claim, while a pattern the
corrected candle no longer produces is a claim that is simply gone.

A reading with no stored counterpart is an insert, not a correction: it is recorded by the normal path and
appears here only by its absence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from virtual_orders.research.models import PatternDetection


@dataclass(frozen=True)
class Reconciliation:
    """`superseded` pairs a stored fact id with the evidence hash of the reading that replaces it."""

    superseded: tuple[tuple[int, str], ...]
    retracted: tuple[int, ...]


def reconcile(
    found: Sequence[PatternDetection], active: Sequence[Mapping[str, Any]]
) -> Reconciliation:
    by_pattern: dict[str, PatternDetection] = {item.pattern: item for item in found}
    hashes = {item.evidence_hash for item in found}
    superseded: list[tuple[int, str]] = []
    retracted: list[int] = []
    for row in active:
        if row["evidence_hash"] in hashes:
            continue  # the same reading survived the revision untouched
        replacement = by_pattern.get(str(row["pattern"]))
        if replacement is None:
            retracted.append(int(row["id"]))
        else:
            superseded.append((int(row["id"]), replacement.evidence_hash))
    return Reconciliation(tuple(superseded), tuple(retracted))
