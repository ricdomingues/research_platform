"""Session-wide hermeticity guard for derandomized Hypothesis property tests.

Root cause, fully investigated in
`.superpowers/sdd/2026-09-12-virtual-order-engine-persistence/flake-investigation.md`:
Hypothesis 6.168.0 ships a "constants from local code" feature
(`hypothesis.internal.conjecture.providers._get_local_constants`, backed by
`hypothesis.internal.constants_ast`) that scans the AST of every newly-imported,
non-stdlib / non-site-packages / non-Hypothesis / non-`test*` source module for
literal `int`/`float`/`str`/`bytes` constants and folds them into a process-global
pool. `HypothesisProvider._maybe_draw_constant` then substitutes values from that
pool into `st.integers()`/`st.floats()`/... draws with probability ``p=0.05`` --
*instead of* a purely random value from the seeded `Random` instance -- even for
tests declaring `derandomize=True, database=None, phases=[Phase.generate]`.

That means a derandomized property test's *generated values* are not determined
solely by its fixed seed: they also depend on which other local source files
happen to already be imported into the same pytest process, i.e. on pytest's
collection order and on which other test modules were selected to run. This is
real, observed behaviour: importing `src/virtual_orders/marketdata/alpaca.py`
(which contains the literal `10000`, landing inside
`tests/fills/test_properties.py`'s own `st.integers(9700, 10400)` /
`st.integers(9800, 10100)` bounds) anywhere else in the same pytest session
deterministically flips
`tests/fills/test_properties.py::test_after_fill_bias_strategy_covers_every_exit_type`'s
frequency assertion.

Naive fix considered and rejected: forcing the local-constants pool to always be
empty (`_get_local_constants` returning `Constants()` unconditionally). Verified
empirically that this *also* changes the outcome of the very same frozen Plan 1
test when run in isolation (no Plan 2 import at all): `src/core/**`'s own literals
(e.g. calendar/session-length constants) are themselves "local" source and were
already being scanned into this pool the whole time Plan 1's property tests were
authored, tuned and tagged -- `test_after_fill_bias_strategy_covers_every_exit_type`
was calibrated against generation that already includes that contribution. Fully
zeroing the pool removes that contribution too and makes the frozen test fail on
its own (`STOPPED_BREAKEVEN` frequency drops from 25/300 to 7/300), which is not
an acceptable "fix" under the Plan 1 freeze.

The mechanism actually applied here filters *which* local modules the scanner is
allowed to see, rather than the pool's contents: `is_local_module_file` (imported
into `hypothesis.internal.conjecture.providers`, and consulted by
`_get_local_constants` on every newly-seen module) is wrapped so only files under
the frozen `src/core/` tree are still treated as "local" and eligible for
scanning -- exactly the set of local modules already being scanned when Plan 1's
property tests were tagged and frozen. Every other local file (any Plan 2 module
under `src/virtual_orders/`, any non-`tests/fills` test module, and anything added
to this repo in the future outside `src/core/`) is now treated as non-local and is
therefore never scanned, no matter when or in what order it gets imported. This:

* reproduces Plan 1's existing, already-tagged-and-passing generation exactly
  (verified: `test_after_fill_bias_strategy_covers_every_exit_type`'s frequency
  counts are bit-for-bit identical with and without `virtual_orders.marketdata.alpaca`
  imported, once this filter is active), and
* makes that generation immune to any future Plan 2 (or other non-core) literal,
  because such files can now never enter the pool regardless of import order.

This file is new (not part of Plan 1's frozen `src/core/` / `tests/fills/`) and
only wires test-session setup; it does not modify any frozen file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_HERMETICITY_ERROR = (
    "Hypothesis internals changed: {detail} The hermeticity guard in "
    "tests/conftest.py (see "
    ".superpowers/sdd/2026-09-12-virtual-order-engine-persistence/"
    "flake-investigation.md) must be re-established for this Hypothesis version "
    "before derandomized property tests can be trusted."
)

# The only local source tree the local-constants scanner is allowed to see:
# Plan 1's frozen core, exactly as it was when tests/fills/ was tagged.
_FROZEN_CORE_ROOT = str((Path(__file__).resolve().parent.parent / "src" / "core").resolve())


def _is_frozen_core_source(path: str) -> bool:
    try:
        resolved = str(Path(path).resolve())
    except OSError:  # pragma: no cover - defensive
        return False
    return resolved == _FROZEN_CORE_ROOT or resolved.startswith(_FROZEN_CORE_ROOT + "/")


def _freeze_hypothesis_local_constants() -> None:
    """Restrict Hypothesis's local-constants scanner to `src/core/` only.

    Wraps `hypothesis.internal.conjecture.providers.is_local_module_file` --
    the predicate `_get_local_constants` consults for every newly-seen module --
    so any local file outside the frozen `src/core/` tree is reported as
    non-local and therefore never scanned into the shared constants pool,
    regardless of import order or which other test files ran in this session.
    """
    try:
        from hypothesis.internal.conjecture import providers as hp_providers
    except ImportError as exc:
        pytest.exit(_HERMETICITY_ERROR.format(detail=f"import failed: {exc}"))
        return

    if not hasattr(hp_providers, "is_local_module_file") or not hasattr(
        hp_providers, "_get_local_constants"
    ):
        pytest.exit(
            _HERMETICITY_ERROR.format(
                detail=(
                    "hypothesis.internal.conjecture.providers.is_local_module_file "
                    "and/or ._get_local_constants no longer exist."
                )
            )
        )
        return

    original_is_local_module_file = hp_providers.is_local_module_file

    def _only_frozen_core_is_local(path: str) -> bool:
        return _is_frozen_core_source(path) and original_is_local_module_file(path)

    hp_providers.is_local_module_file = _only_frozen_core_is_local


def pytest_configure(config: pytest.Config) -> None:
    _freeze_hypothesis_local_constants()
