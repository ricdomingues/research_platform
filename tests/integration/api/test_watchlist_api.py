from decimal import Decimal

from tests.integration.support import count, post_json


def rule_body(**overrides):
    body = {"kind": "PRICE_CROSS", "level": Decimal("101.50"), "direction": "ABOVE"}
    body.update(overrides)
    return body


def test_adding_a_ticker_checks_it_once_and_is_idempotent(api):
    first = api.client.put("/watchlist/MSFT")
    assert first.status_code == 201 and first.json() == {"ticker": "MSFT", "status": "CREATED"}
    again = api.client.put("/watchlist/MSFT")
    assert again.status_code == 200 and again.json() == {"ticker": "MSFT", "status": "EXISTING"}
    assert api.tickers.calls == ["MSFT"]
    assert api.client.get("/watchlist").json() == {
        "watchlist": [{"ticker": "MSFT", "added_at": api.clock.now.isoformat(), "rules": []}]
    }


def test_untradable_is_422_and_unverifiable_is_503_without_storing(api):
    api.tickers.untradable["ZZZZ"] = "INACTIVE"
    untradable = api.client.put("/watchlist/ZZZZ")
    assert untradable.status_code == 422
    assert untradable.json()["error"] == {"code": "TICKER_NOT_TRADABLE", "reason": "INACTIVE",
                                          "detail": {"ticker": "ZZZZ"}}
    api.tickers.failing = True
    unavailable = api.client.put("/watchlist/MSFT")
    assert unavailable.status_code == 503 and unavailable.json()["error"]["code"] == "TICKER_UNVERIFIABLE"
    assert "(fake)" not in unavailable.text
    assert count(api.services.engine, "watchlist") == 0


def test_rules_are_created_listed_and_deleted_with_exact_decimals(api):
    api.client.put("/watchlist/MSFT")
    created = post_json(api.client, "/watchlist/MSFT/alerts", rule_body())
    assert created.status_code == 201
    rule = created.json()["rule"]
    assert (rule["ticker"], rule["kind"], rule["level"], rule["direction"]) == ("MSFT", "PRICE_CROSS", "101.5", "ABOVE")
    assert rule["cooldown_minutes"] == 30 and rule["created_at"] == api.clock.now.isoformat()
    pressure = post_json(api.client, "/watchlist/MSFT/alerts",
                         {"kind": "PRESSURE", "cmf_threshold": Decimal("0.35"), "window_bars": 20, "cooldown_minutes": 0})
    assert pressure.status_code == 201 and pressure.json()["rule"]["cmf_threshold"] == "0.35"

    listed = api.client.get("/watchlist").json()["watchlist"][0]["rules"]
    assert {item["id"] for item in listed} == {rule["id"], pressure.json()["rule"]["id"]}

    removed = api.client.delete(f"/alerts/{rule['id']}")
    assert removed.status_code == 200 and removed.json() == {"rule_id": rule["id"], "removed": True}
    again = api.client.delete(f"/alerts/{rule['id']}")
    assert again.status_code == 404 and again.json()["error"]["code"] == "ALERT_RULE_NOT_FOUND"


def test_invalid_rules_unknown_tickers_and_bad_json(api):
    invalid = post_json(api.client, "/watchlist/MSFT/alerts", {"kind": "PRICE_CROSS"})
    assert invalid.status_code == 422
    assert invalid.json()["error"] == {"code": "ALERT_RULE_INVALID", "reason": None,
                                       "detail": {"errors": ["MISSING_FIELD:level", "MISSING_FIELD:direction"]}}
    unknown = post_json(api.client, "/watchlist/MSFT/alerts", rule_body())
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "WATCHLIST_TICKER_NOT_FOUND"
    missing = api.client.delete("/watchlist/MSFT")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "WATCHLIST_TICKER_NOT_FOUND"
    bad_json = api.client.post("/watchlist/MSFT/alerts", content="[1]", headers={"Content-Type": "application/json"})
    assert bad_json.status_code == 422
    assert bad_json.json()["error"] == {"code": "INVALID_JSON", "reason": None, "detail": {"code": "NOT_AN_OBJECT"}}
    truncated = api.client.post("/watchlist/MSFT/alerts", content="{", headers={"Content-Type": "application/json"})
    assert truncated.status_code == 422
    error = truncated.json()["error"]
    assert error["code"] == "INVALID_JSON" and "message" not in error["detail"]  # I3: no decoder text, ever
    bad_id = api.client.delete("/alerts/not-a-uuid")
    assert bad_id.status_code == 422 and bad_id.json()["error"]["code"] == "REQUEST_INVALID"


def test_removing_a_ticker_removes_its_rules(api):
    api.client.put("/watchlist/MSFT")
    post_json(api.client, "/watchlist/MSFT/alerts", rule_body())
    removed = api.client.delete("/watchlist/MSFT")
    assert removed.status_code == 200 and removed.json() == {"ticker": "MSFT", "removed": True}
    assert count(api.services.engine, "alert_rules") == 0 and api.client.get("/watchlist").json() == {"watchlist": []}


def test_tickers_are_normalized_and_blank_ones_rejected(api):
    created = api.client.put("/watchlist/%20msft%20")
    assert created.status_code == 201 and created.json() == {"ticker": "MSFT", "status": "CREATED"}
    assert api.tickers.calls == ["MSFT"]
    assert post_json(api.client, "/watchlist/msft/alerts", rule_body()).json()["rule"]["ticker"] == "MSFT"

    for raw_ticker in ("%20", "BRK%20B"):
        rejected = api.client.put(f"/watchlist/{raw_ticker}")
        assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "TICKER_INVALID"
    assert api.tickers.calls == ["MSFT"]  # an invalid ticker never reaches the provider
    assert api.client.delete("/watchlist/msft").json() == {"ticker": "MSFT", "removed": True}
