from decimal import Decimal

import pytest

from core.domain.models import FillConfig, ZoneLostPolicy
from tests.config_support import BASE_ENV
from virtual_orders.config import ENV_VARIABLES, ConfigError, load_settings

REQUIRED = ("API_KEY", "DATABASE_URL", "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FMP_API_KEY")


def test_defaults_follow_spec_section_8():
    settings = load_settings(BASE_ENV)
    assert settings.code_version == "unknown"
    assert settings.eval_interval_minutes == 2
    assert settings.fill_config == FillConfig()
    assert (settings.bootstrap_resamples, settings.bootstrap_seed) == (2000, 42)
    assert settings.n8n_webhook_url is None
    assert settings.price_source == "alpaca_iex"
    assert settings.alpaca_trading_url == "https://paper-api.alpaca.markets"
    assert (settings.api_key, settings.database_url) == ("test-api-key", BASE_ENV["DATABASE_URL"])


def test_every_variable_maps_to_settings_and_fill_config():
    env = {
        **BASE_ENV, "GIT_SHA": "abc123", "EVAL_INTERVAL_MINUTES": "5", "DEFAULT_RISK_AMOUNT": "250",
        "ENTRY_SLIPPAGE_BPS": "2", "STOP_SLIPPAGE_BPS": "7.5", "COMMISSION_PER_EXECUTION": "0.35",
        "SEC_TAF_FEES_ENABLED": "true", "SEC_FEE_RATE": "0.0000278", "TAF_FEE_PER_SHARE": "0.000166",
        "TAF_FEE_MAX": "8.30", "ZONE_LOST_POLICY": "CANCEL", "TARGET1_SCALE_OUT_PCT": "40",
        "DATA_GAP_MINUTES": "15", "CROSSCHECK_TOLERANCE_PCT": "0.25", "DIVIDEND_TOLERANCE": "0.01",
        "BOOTSTRAP_RESAMPLES": "500", "BOOTSTRAP_SEED": "7", "N8N_WEBHOOK_URL": "https://n8n.example/hook",
        "PRICE_SOURCE": "fake_feed", "ALPACA_TRADING_URL": "https://api.alpaca.markets",
    }
    assert set(env) == set(ENV_VARIABLES) and len(ENV_VARIABLES) == len(set(ENV_VARIABLES))
    settings = load_settings(env)
    assert settings.fill_config == FillConfig(
        risk_amount=Decimal("250"), entry_slippage_bps=Decimal("2"), stop_slippage_bps=Decimal("7.5"),
        commission_per_execution=Decimal("0.35"), sec_taf_fees_enabled=True, sec_fee_rate=Decimal("0.0000278"),
        taf_fee_per_share=Decimal("0.000166"), taf_fee_max=Decimal("8.30"), zone_lost_policy=ZoneLostPolicy.CANCEL,
        target1_scale_out_pct=Decimal("40"), data_gap_minutes=15, crosscheck_tolerance_pct=Decimal("0.25"),
        dividend_tolerance=Decimal("0.01"),
    )
    assert settings.code_version == "abc123" and settings.eval_interval_minutes == 5
    assert (settings.bootstrap_resamples, settings.bootstrap_seed) == (500, 7)
    assert settings.n8n_webhook_url == "https://n8n.example/hook"
    assert settings.price_source == "fake_feed" and settings.alpaca_trading_url == "https://api.alpaca.markets"


def test_missing_required_variables_are_all_reported():
    with pytest.raises(ConfigError) as caught:
        load_settings({"API_KEY": "  "})
    assert caught.value.errors == [f"MISSING:{name}" for name in REQUIRED]


@pytest.mark.parametrize(("name", "value", "error"), [
    ("EVAL_INTERVAL_MINUTES", "two", "INVALID_INTEGER:EVAL_INTERVAL_MINUTES"),
    ("EVAL_INTERVAL_MINUTES", "0", "OUT_OF_RANGE:EVAL_INTERVAL_MINUTES"),
    ("BOOTSTRAP_SEED", "-1", "OUT_OF_RANGE:BOOTSTRAP_SEED"),
    ("DEFAULT_RISK_AMOUNT", "NaN", "INVALID_DECIMAL:DEFAULT_RISK_AMOUNT"),
    ("DEFAULT_RISK_AMOUNT", "abc", "INVALID_DECIMAL:DEFAULT_RISK_AMOUNT"),
    ("SEC_TAF_FEES_ENABLED", "maybe", "INVALID_BOOLEAN:SEC_TAF_FEES_ENABLED"),
    ("ZONE_LOST_POLICY", "SOMETIMES", "INVALID_CHOICE:ZONE_LOST_POLICY"),
    ("DEFAULT_RISK_AMOUNT", "-5", "INVALID_FILL_CONFIG:risk_amount must be positive"),
    ("SEC_TAF_FEES_ENABLED", "true", "FEES_ENABLED_WITHOUT_RATES:SEC_TAF_FEES_ENABLED"),
])
def test_invalid_values_name_the_variable(name, value, error):
    with pytest.raises(ConfigError) as caught:
        load_settings({**BASE_ENV, name: value})
    assert caught.value.errors == [error]


def test_secrets_never_appear_in_repr_or_errors():
    secret = "super-secret-value"
    settings = load_settings({**BASE_ENV, "API_KEY": secret, "ALPACA_SECRET_KEY": secret, "FMP_API_KEY": secret})
    assert secret not in repr(settings)
    with pytest.raises(ConfigError) as caught:
        load_settings({**BASE_ENV, "FMP_API_KEY": "", "DEFAULT_RISK_AMOUNT": secret, "ZONE_LOST_POLICY": secret})
    assert secret not in str(caught.value) and all(secret not in e for e in caught.value.errors)
