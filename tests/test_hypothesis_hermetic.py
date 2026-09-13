"""Regression test for the hermeticity guard in `tests/conftest.py`.

See `tests/conftest.py` and
`.superpowers/sdd/2026-09-12-virtual-order-engine-persistence/flake-investigation.md`
for the full root cause. This proves the guard is actually active: importing
`virtual_orders.marketdata.alpaca` -- which contains the literal `10000` that
Hypothesis's local-constants scanner would otherwise pick up and that lands
inside `tests/fills/test_properties.py`'s own integer strategy bounds -- never
lets `10000` enter Hypothesis's local-constants pool, because that module lives
outside the frozen `src/core/` tree the guard restricts scanning to.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis.internal.conjecture import providers as hp_providers

REPO_ROOT = Path(__file__).resolve().parent.parent
ALPACA_PATH = str(REPO_ROOT / "src" / "virtual_orders" / "marketdata" / "alpaca.py")
CORE_MODELS_PATH = str(REPO_ROOT / "src" / "core" / "domain" / "models.py")


def test_guard_replaced_is_local_module_file() -> None:
    # Confirms tests/conftest.py actually ran and patched this process, rather
    # than any of the assertions below passing by coincidence.
    assert hp_providers.is_local_module_file.__qualname__.endswith(
        "_only_frozen_core_is_local"
    )


def test_virtual_orders_source_is_never_treated_as_local() -> None:
    assert hp_providers.is_local_module_file(ALPACA_PATH) is False


def test_frozen_core_source_is_still_treated_as_local() -> None:
    assert hp_providers.is_local_module_file(CORE_MODELS_PATH) is True


def test_local_constants_pool_excludes_alpaca_literal_even_when_imported() -> None:
    import virtual_orders.marketdata.alpaca  # noqa: F401 - import for its side effect

    constants = hp_providers._get_local_constants()

    assert 10000 not in constants.integers
