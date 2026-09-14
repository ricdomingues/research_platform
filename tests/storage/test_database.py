from virtual_orders.storage import database


def test_engine_sets_connect_timeout_utc_and_libpq_keepalives(monkeypatch):
    captured: dict = {}

    def fake_create_engine(url, **kwargs):
        captured.update(url=url, **kwargs)
        return "engine"

    monkeypatch.setattr(database, "create_engine", fake_create_engine)
    assert database.make_engine("postgresql+psycopg://vo@db/vo") == "engine"
    assert captured["pool_pre_ping"] is True
    assert captured["connect_args"] == {
        "options": "-c timezone=UTC", "connect_timeout": 5,
        "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3,
        "tcp_user_timeout": 60000,
    }
