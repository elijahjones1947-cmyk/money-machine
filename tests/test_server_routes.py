"""
Tests for server.py's Flask routes. See tests/conftest.py's module
docstring for how server.py gets imported without a real Postgres
instance or real broker network calls (a fake DB pool + fake broker
methods, not mocks of db.py's/server.py's own functions).

Covers: /api/dashboard, /api/login (valid/invalid password, failed-attempt
tracking), /webhook (valid secret, invalid secret, WEBHOOK_IP_MODE
allowlist behavior, dedup-stamp rollback on failed attempts, duplicate
drop of a genuinely successful signal), /api/settings (risk_caps
persistence), and /api/backtest (live_performance present).

NOT covered: /api/manual_trade, /api/watchlist, /ui/layout, Hermes's
routes -- these share the same _process_trade_signal/db plumbing
already exercised below and don't need separate fixture infrastructure,
but weren't asked for here. Worth adding the same way if this suite
grows further.
"""

import json
import logging

import config
from errors import BrokerConnectionError


def test_login_valid_password_succeeds(client):
    resp = client.post("/api/login", json={"password": "test-dashboard-password"})
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_login_invalid_password_rejected(client):
    resp = client.post("/api/login", json={"password": "wrong"})
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "invalid password"}


def test_login_tracks_failed_attempts_and_clears_on_success(client):
    import state

    client.post("/api/login", json={"password": "wrong"})
    client.post("/api/login", json={"password": "also wrong"})
    assert len(state.failed_login_attempts) == 2

    resp = client.post("/api/login", json={"password": "test-dashboard-password"})
    assert resp.status_code == 200
    assert state.failed_login_attempts == []


def test_webhook_valid_secret_places_order(client):
    resp = client.post("/webhook", json={
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "order placed"
    assert body["symbol"] == "AAPL"
    assert body["asset_class"] == "stock"


def test_webhook_invalid_secret_rejected(client):
    resp = client.post("/webhook", json={
        "secret": "wrong", "action": "buy", "symbol": "AAPL",
    })
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "unauthorized"}


def test_webhook_invalid_secret_tracks_failed_attempts(client):
    import state

    for _ in range(3):
        client.post("/webhook", json={"secret": "wrong", "action": "buy", "symbol": "AAPL"})
    assert len(state.failed_webhook_attempts) == 3


def test_webhook_retry_after_broker_failure_succeeds(client, monkeypatch):
    """A failed attempt must NOT leave the 60s dedup stamp behind: if the
    broker errors on the first try, TradingView's retry of the same alert
    within the window has to actually execute -- the original bug was the
    retry coming back 200 'duplicate ignored' with the signal never traded
    and nothing in the logs."""
    import server
    import state

    calls = []

    def flaky_place_order(symbol, side, size, order_type="market"):
        calls.append((symbol, side, size))
        if len(calls) == 1:
            raise BrokerConnectionError("alpaca unreachable")
        return {"id": "fake-order-id", "status": "filled"}

    monkeypatch.setattr(server.alpaca_broker, "place_order", flaky_place_order)

    payload = {"secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL"}
    first = client.post("/webhook", json=payload)
    assert first.status_code == 502

    # The failed attempt rolled back its dedup stamp...
    assert "AAPL_buy" not in state.last_signal_time

    # ...so the retry places a real order instead of 'duplicate ignored'.
    retry = client.post("/webhook", json=payload)
    assert retry.status_code == 200
    assert retry.get_json()["status"] == "order placed"
    assert len(calls) == 2


def test_webhook_rejected_sell_rolls_back_dedup_stamp(client):
    """Same rollback guarantee on a pre-broker rejection path (here: sell
    with no held position) -- not just on broker exceptions."""
    import state

    resp = client.post("/webhook", json={
        "secret": "test-webhook-secret", "action": "sell", "symbol": "AAPL",
    })
    assert resp.status_code == 400
    assert "AAPL_sell" not in state.last_signal_time


def test_webhook_duplicate_successful_signal_rejected_and_logged(client, monkeypatch, caplog):
    """A genuinely duplicate signal (first one succeeded) within 60s must
    still be dropped -- and, new with the rollback fix, the drop must show
    up in the logs instead of being silent."""
    import server

    calls = []

    def counting_place_order(symbol, side, size, order_type="market"):
        calls.append(symbol)
        return {"id": "fake-order-id", "status": "filled"}

    monkeypatch.setattr(server.alpaca_broker, "place_order", counting_place_order)

    payload = {"secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL"}
    first = client.post("/webhook", json=payload)
    assert first.status_code == 200
    assert first.get_json()["status"] == "order placed"

    with caplog.at_level(logging.INFO):
        dup = client.post("/webhook", json=payload)
    assert dup.status_code == 200
    assert dup.get_json() == {"status": "duplicate ignored"}
    assert any("Dropped duplicate buy stock AAPL" in r.getMessage() for r in caplog.records)

    # Only the first request ever reached the broker.
    assert calls == ["AAPL"]


def test_webhook_ip_allowlist_enforce_rejects_unlisted_ip(client, monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_IP_MODE", "enforce")
    # No X-Forwarded-For header -- Flask's test client's remote_addr
    # ('127.0.0.1') isn't in TradingView's published IP list.
    resp = client.post("/webhook", json={
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "unauthorized"}


def test_webhook_ip_allowlist_enforce_allows_listed_ip(client, monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_IP_MODE", "enforce")
    allowed_ip = next(iter(config.TRADINGVIEW_WEBHOOK_IPS))
    resp = client.post(
        "/webhook",
        json={"secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL"},
        headers={"X-Forwarded-For": allowed_ip},
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "order placed"


def test_webhook_ip_allowlist_log_mode_does_not_reject(client, monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_IP_MODE", "log")
    # Same unlisted remote_addr as the enforce-mode rejection test above --
    # log mode must NOT block the trade, only (per server.py) log a warning.
    resp = client.post("/webhook", json={
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "order placed"


def test_settings_updates_risk_caps_and_persists(auth_client, db_store):
    import state

    resp = auth_client.post("/api/settings", json={
        "asset_class": "stock",
        "max_position_size_pct": 8,
        "max_daily_loss_pct": 4,
        "max_open_positions": 7,
        "safety_stop_loss_pct": 1.5,
    })
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "updated"}

    # In-memory risk_caps -- the dict RiskManager actually enforces --
    # updated with the *_pct fields divided by 100 (UI/API sends plain
    # percentages, state.risk_caps stores fractions).
    assert state.risk_caps["stock"]["max_position_size_pct"] == 0.08
    assert state.risk_caps["stock"]["max_daily_loss_pct"] == 0.04
    assert state.risk_caps["stock"]["max_open_positions"] == 7
    assert state.risk_caps["stock"]["safety_stop_loss_pct"] == 0.015

    # And written through to the DB (fake), under the same shape server.py saves.
    saved = json.loads(db_store["settings"]["risk_caps"])
    assert saved["stock"]["max_position_size_pct"] == 0.08
    assert saved["stock"]["max_open_positions"] == 7


def test_settings_requires_auth(client):
    resp = client.post("/api/settings", json={"asset_class": "stock", "max_open_positions": 7})
    assert resp.status_code == 401


def test_dashboard_returns_combined_equity_and_positions(auth_client):
    resp = auth_client.get("/api/dashboard")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["combined_equity"] == 20000.0  # fake alpaca (10000) + fake oanda (10000)
    assert body["positions"] == []
    assert body["bot_enabled"] is True
    assert "risk_caps" in body


def test_dashboard_requires_auth(client):
    resp = client.get("/api/dashboard")
    assert resp.status_code == 401


def test_backtest_response_includes_live_performance_key(auth_client):
    resp = auth_client.get("/api/backtest")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "live_performance" in body
    # state.trade_log is empty (reset_state), so this is the no-closed-trades shape.
    assert body["live_performance"]["trade_count"] == 0
