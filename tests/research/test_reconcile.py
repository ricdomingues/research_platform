"""What a revisited bucket does to the facts already stored for it (Plan 6, D96)."""

from __future__ import annotations

from tests.research.support import detection
from virtual_orders.research.reconcile import reconcile


def stored(fact_id: int, pattern: str, evidence_hash: str) -> dict[str, object]:
    return {"id": fact_id, "pattern": pattern, "evidence_hash": evidence_hash,
            "input_content_hash": "old"}


def test_an_identical_reading_changes_nothing():
    found = [detection(pattern="HAMMER")]
    active = [stored(1, "HAMMER", found[0].evidence_hash)]
    result = reconcile(found, active)
    assert result.superseded == () and result.retracted == ()


def test_a_changed_reading_of_the_same_pattern_supersedes_the_old_one():
    """Case A of D96: the corrected candle still says HAMMER, but not the same HAMMER."""
    found = [detection(pattern="HAMMER", geometry_score="0.90")]
    active = [stored(1, "HAMMER", "a-different-hash")]
    result = reconcile(found, active)
    assert result.superseded == ((1, found[0].evidence_hash),)
    assert result.retracted == ()


def test_a_pattern_that_no_longer_appears_is_retracted():
    """Case B of D96, and the one the Plan 5 canary could not record."""
    found = [detection(pattern="DOJI")]
    active = [stored(1, "HAMMER", "whatever")]
    result = reconcile(found, active)
    assert result.retracted == (1,)
    assert result.superseded == ()


def test_a_bucket_that_now_detects_nothing_retracts_everything_it_had():
    active = [stored(1, "HAMMER", "x"), stored(2, "DOJI", "y")]
    result = reconcile([], active)
    assert result.retracted == (1, 2)


def test_a_new_pattern_with_no_stored_counterpart_is_not_a_supersession():
    """A reading that simply did not exist before is an insert, not a correction of something else."""
    found = [detection(pattern="HAMMER"), detection(pattern="DOJI")]
    active = [stored(1, "HAMMER", found[0].evidence_hash)]
    result = reconcile(found, active)
    assert result.superseded == () and result.retracted == ()
