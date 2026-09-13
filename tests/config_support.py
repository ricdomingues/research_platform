"""Minimal valid environment for configuration and composition tests. Values are fake."""

BASE_ENV: dict[str, str] = {
    "API_KEY": "test-api-key",
    "DATABASE_URL": "postgresql+psycopg://vo:vo@127.0.0.1:1/unused",
    "ALPACA_API_KEY": "alpaca-key",
    "ALPACA_SECRET_KEY": "alpaca-secret",
    "FMP_API_KEY": "fmp-key",
}
