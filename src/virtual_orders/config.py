"""Environment configuration (spec 8, D13), read once at startup. Errors name variables, never values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from core.domain.models import FillConfig, ZoneLostPolicy

DEFAULT_PRICE_SOURCE = "alpaca_iex"
DEFAULT_ALPACA_TRADING_URL = "https://paper-api.alpaca.markets"
REQUIRED = ("API_KEY", "DATABASE_URL", "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FMP_API_KEY")
OPTIONAL = (
    "GIT_SHA", "EVAL_INTERVAL_MINUTES", "DEFAULT_RISK_AMOUNT", "ENTRY_SLIPPAGE_BPS", "STOP_SLIPPAGE_BPS",
    "COMMISSION_PER_EXECUTION", "SEC_TAF_FEES_ENABLED", "SEC_FEE_RATE", "TAF_FEE_PER_SHARE", "TAF_FEE_MAX",
    "ZONE_LOST_POLICY", "TARGET1_SCALE_OUT_PCT", "DATA_GAP_MINUTES", "CROSSCHECK_TOLERANCE_PCT",
    "DIVIDEND_TOLERANCE", "BOOTSTRAP_RESAMPLES", "BOOTSTRAP_SEED", "N8N_WEBHOOK_URL", "PRICE_SOURCE",
    "ALPACA_TRADING_URL",
)
ENV_VARIABLES = REQUIRED + OPTIONAL
_TRUE = frozenset({"true", "1", "yes"})
_FALSE = frozenset({"false", "0", "no"})


class ConfigError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True, repr=False)
class Settings:
    api_key: str
    database_url: str
    alpaca_api_key: str
    alpaca_secret_key: str
    fmp_api_key: str
    code_version: str
    eval_interval_minutes: int
    fill_config: FillConfig
    bootstrap_resamples: int
    bootstrap_seed: int
    n8n_webhook_url: str | None
    price_source: str
    alpaca_trading_url: str

    def __repr__(self) -> str:  # secrets and the database URL (may hold a password) stay out of logs
        return (
            f"Settings(code_version={self.code_version!r}, price_source={self.price_source!r}, "
            f"eval_interval_minutes={self.eval_interval_minutes}, fill_config={self.fill_config!r})"
        )


class _Reader:
    def __init__(self, environ: Mapping[str, str]) -> None:
        self._env = environ
        self.errors: list[str] = []

    def _raw(self, name: str) -> str:
        return self._env.get(name, "").strip()

    def required(self, name: str) -> str:
        value = self._raw(name)
        if not value:
            self.errors.append(f"MISSING:{name}")
        return value

    def text(self, name: str, default: str) -> str:
        return self._raw(name) or default

    def integer(self, name: str, default: int, minimum: int) -> int:
        raw = self._raw(name)
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            self.errors.append(f"INVALID_INTEGER:{name}")
            return default
        if value < minimum:
            self.errors.append(f"OUT_OF_RANGE:{name}")
            return default
        return value

    def decimal(self, name: str, default: Decimal) -> Decimal:
        raw = self._raw(name)
        if not raw:
            return default
        try:
            value = Decimal(raw)
        except InvalidOperation:
            self.errors.append(f"INVALID_DECIMAL:{name}")
            return default
        if not value.is_finite():
            self.errors.append(f"INVALID_DECIMAL:{name}")
            return default
        return value

    def boolean(self, name: str, default: bool) -> bool:
        raw = self._raw(name).lower()
        if not raw:
            return default
        if raw in _TRUE:
            return True
        if raw in _FALSE:
            return False
        self.errors.append(f"INVALID_BOOLEAN:{name}")
        return default

    def zone_lost_policy(self, name: str, default: ZoneLostPolicy) -> ZoneLostPolicy:
        raw = self._raw(name)
        if not raw:
            return default
        try:
            return ZoneLostPolicy(raw)
        except ValueError:
            self.errors.append(f"INVALID_CHOICE:{name}")
            return default


def _fill_config(read: _Reader) -> FillConfig:
    base = FillConfig()
    risk_amount = read.decimal("DEFAULT_RISK_AMOUNT", base.risk_amount)
    entry_slippage_bps = read.decimal("ENTRY_SLIPPAGE_BPS", base.entry_slippage_bps)
    stop_slippage_bps = read.decimal("STOP_SLIPPAGE_BPS", base.stop_slippage_bps)
    commission = read.decimal("COMMISSION_PER_EXECUTION", base.commission_per_execution)
    fees_enabled = read.boolean("SEC_TAF_FEES_ENABLED", base.sec_taf_fees_enabled)
    sec_fee_rate = read.decimal("SEC_FEE_RATE", base.sec_fee_rate)
    taf_fee_per_share = read.decimal("TAF_FEE_PER_SHARE", base.taf_fee_per_share)
    taf_fee_max = read.decimal("TAF_FEE_MAX", base.taf_fee_max)
    zone_lost_policy = read.zone_lost_policy("ZONE_LOST_POLICY", base.zone_lost_policy)
    scale_out = read.decimal("TARGET1_SCALE_OUT_PCT", base.target1_scale_out_pct)
    data_gap_minutes = read.integer("DATA_GAP_MINUTES", base.data_gap_minutes, minimum=1)
    crosscheck = read.decimal("CROSSCHECK_TOLERANCE_PCT", base.crosscheck_tolerance_pct)
    dividend_tolerance = read.decimal("DIVIDEND_TOLERANCE", base.dividend_tolerance)
    if fees_enabled and sec_fee_rate == 0 and taf_fee_per_share == 0 and taf_fee_max == 0:
        read.errors.append("FEES_ENABLED_WITHOUT_RATES:SEC_TAF_FEES_ENABLED")  # never silently zero fees (D13)
    try:
        return FillConfig(
            risk_amount=risk_amount, entry_slippage_bps=entry_slippage_bps, stop_slippage_bps=stop_slippage_bps,
            commission_per_execution=commission, sec_taf_fees_enabled=fees_enabled, sec_fee_rate=sec_fee_rate,
            taf_fee_per_share=taf_fee_per_share, taf_fee_max=taf_fee_max, zone_lost_policy=zone_lost_policy,
            target1_scale_out_pct=scale_out, data_gap_minutes=data_gap_minutes,
            crosscheck_tolerance_pct=crosscheck, dividend_tolerance=dividend_tolerance,
        )
    except (TypeError, ValueError) as exc:  # FillConfig messages name fields, never raw env values
        read.errors.append(f"INVALID_FILL_CONFIG:{exc}")
        return base


def load_settings(environ: Mapping[str, str]) -> Settings:
    read = _Reader(environ)
    required = {name: read.required(name) for name in REQUIRED}
    settings = Settings(
        api_key=required["API_KEY"],
        database_url=required["DATABASE_URL"],
        alpaca_api_key=required["ALPACA_API_KEY"],
        alpaca_secret_key=required["ALPACA_SECRET_KEY"],
        fmp_api_key=required["FMP_API_KEY"],
        code_version=read.text("GIT_SHA", "unknown"),
        eval_interval_minutes=read.integer("EVAL_INTERVAL_MINUTES", 2, minimum=1),
        fill_config=_fill_config(read),
        bootstrap_resamples=read.integer("BOOTSTRAP_RESAMPLES", 2000, minimum=1),
        bootstrap_seed=read.integer("BOOTSTRAP_SEED", 42, minimum=0),
        n8n_webhook_url=read.text("N8N_WEBHOOK_URL", "") or None,
        price_source=read.text("PRICE_SOURCE", DEFAULT_PRICE_SOURCE),
        alpaca_trading_url=read.text("ALPACA_TRADING_URL", DEFAULT_ALPACA_TRADING_URL),
    )
    if read.errors:
        raise ConfigError(read.errors)
    return settings
