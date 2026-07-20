"""
Tests for Phase 4: strategy management API routes, /webhook's
strategy_id validation gate, and /api/strategies/assign's force-close-
on-switch behavior.
"""

import time
from types import SimpleNamespace

import db
import webhook_queue


def _post_webhook_and_wait(client, payload, **kwargs):
    resp = client.post("/webhook", json=payload, **kwargs)
    webhook_queue.wait_for_idle(payload.get("symbol"))
    return resp


# --- /api/strategies (GET/POST) --------------------------------------------

def test_api_strategies_requires_auth(client):
    resp = client.get("/api/strategies")
    assert resp.status_code == 401
    resp = client.post("/api/strategies", json={"name": "X", "params": {}})
    assert resp.status_code == 401


def test_api_strategies_post_creates_a_new_version(auth_client, db_store):
    resp = auth_client.post("/api/strategies", json={
        "name": "HHB - Stock", "params": {"lookback": 7}, "description": "test",
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["name"] == "HHB - Stock"
    assert body["version"] == 1


def test_api_strategies_post_requires_name_and_params(auth_client):
    resp = auth_client.post("/api/strategies", json={"name": "X"})
    assert resp.status_code == 400
    resp = auth_client.post("/api/strategies", json={"params": {"lookback": 7}})
    assert resp.status_code == 400


def test_api_strategies_get_lists_created_strategies(auth_client, db_store):
    auth_client.post("/api/strategies", json={"name": "A", "params": {"lookback": 7}})
    auth_client.post("/api/strategies", json={"name": "B", "params": {"lookback": 7}})
    resp = auth_client.get("/api/strategies")
    assert resp.status_code == 200
    names = {s["name"] for s in resp.get_json()["strategies"]}
    assert {"A", "B"}.issubset(names)


# --- /api/strategies/assignments (GET) --------------------------------------

def test_api_strategy_assignments_requires_auth(client):
    resp = client.get("/api/strategies/assignments")
    assert resp.status_code == 401


def test_api_strategy_assignments_lists_current_assignments(auth_client, db_store):
    create = auth_client.post("/api/strategies", json={"name": "HHB - Stock", "params": {"lookback": 7}})
    strategy_id = create.get_json()["id"]
    db.assign_strategy_to_symbol("AAPL", strategy_id)

    resp = auth_client.get("/api/strategies/assignments")
    assert resp.status_code == 200
    assignments = {a["symbol"]: a["id"] for a in resp.get_json()["assignments"]}
    assert assignments["AAPL"] == strategy_id


# --- /api/strategies/assign (POST) -- the switch + force-close route -------

def test_api_assign_strategy_requires_auth(client):
    resp = client.post("/api/strategies/assign", json={"symbol": "AAPL", "strategy_id": 1})
    assert resp.status_code == 401


def test_api_assign_strategy_requires_symbol_and_strategy_id(auth_client):
    resp = auth_client.post("/api/strategies/assign", json={"symbol": "AAPL"})
    assert resp.status_code == 400
    resp = auth_client.post("/api/strategies/assign", json={"strategy_id": 1})
    assert resp.status_code == 400


def test_api_assign_strategy_rejects_unknown_strategy_id(auth_client):
    resp = auth_client.post("/api/strategies/assign", json={"symbol": "AAPL", "strategy_id": 999999})
    assert resp.status_code == 404


def test_api_assign_strategy_with_no_open_position_just_reassigns(auth_client, db_store):
    strategy = auth_client.post("/api/strategies", json={"name": "HHB - Stock", "params": {"lookback": 7}}).get_json()

    resp = auth_client.post("/api/strategies/assign", json={"symbol": "AAPL", "strategy_id": strategy["id"]})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "assigned"
    assert body["closed_position"] is False
    assert db.get_symbol_strategy_assignment("AAPL")["id"] == strategy["id"]


def test_api_assign_strategy_force_closes_an_open_long_position_first(auth_client, monkeypatch, db_store):
    import server
    import state

    strategy = auth_client.post("/api/strategies", json={"name": "HHB - Stock", "params": {"lookback": 7}}).get_json()

    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="31", qty_available="31",
        avg_entry_price="200.00", current_price="210.00", unrealized_pl="310.00",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])

    resp = auth_client.post("/api/strategies/assign", json={"symbol": "AAPL", "strategy_id": strategy["id"]})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["closed_position"] is True

    last_trade = state.trade_log[-1]
    assert last_trade["symbol"] == "AAPL"
    assert last_trade["action"] == "sell"  # closed the long
    assert last_trade["source"] == "strategy_switch"
    assert db.get_symbol_strategy_assignment("AAPL")["id"] == strategy["id"]


def test_api_assign_strategy_force_close_uses_the_old_assignment_not_the_new_one(auth_client, monkeypatch, db_store):
    """Ordering guarantee: the force-close must happen under the OLD
    strategy's context (its trade_log entry's strategy_id should point
    at the strategy that was active BEFORE the switch), not the new one
    -- proves close-then-reassign, not reassign-then-close."""
    import server
    import state

    old_strategy = auth_client.post("/api/strategies", json={"name": "Old", "params": {"lookback": 5}}).get_json()
    new_strategy = auth_client.post("/api/strategies", json={"name": "New", "params": {"lookback": 9}}).get_json()
    db.assign_strategy_to_symbol("AAPL", old_strategy["id"])

    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="31", qty_available="31",
        avg_entry_price="200.00", current_price="210.00", unrealized_pl="310.00",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])

    auth_client.post("/api/strategies/assign", json={"symbol": "AAPL", "strategy_id": new_strategy["id"]})

    assert state.trade_log[-1]["strategy_id"] == old_strategy["id"]
    assert db.get_symbol_strategy_assignment("AAPL")["id"] == new_strategy["id"]


def test_api_assign_strategy_does_not_reassign_if_force_close_fails(auth_client, monkeypatch, db_store):
    """Force-close-on-switch is a GUARANTEE, not best-effort -- if the
    close fails, the assignment must stay exactly as it was."""
    import server
    from errors import BrokerConnectionError

    old_strategy = auth_client.post("/api/strategies", json={"name": "Old", "params": {"lookback": 5}}).get_json()
    new_strategy = auth_client.post("/api/strategies", json={"name": "New", "params": {"lookback": 9}}).get_json()
    db.assign_strategy_to_symbol("AAPL", old_strategy["id"])

    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="31", qty_available="31",
        avg_entry_price="200.00", current_price="210.00", unrealized_pl="310.00",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])

    def flaky_place_order(symbol, side, size, order_type="market"):
        raise BrokerConnectionError("alpaca unreachable")

    monkeypatch.setattr(server.alpaca_broker, "place_order", flaky_place_order)

    resp = auth_client.post("/api/strategies/assign", json={"symbol": "AAPL", "strategy_id": new_strategy["id"]})
    assert resp.status_code == 502
    assert db.get_symbol_strategy_assignment("AAPL")["id"] == old_strategy["id"]  # unchanged


def test_api_assign_strategy_force_closes_a_short_forex_position_by_buying_back(auth_client, monkeypatch, db_store):
    import server
    import state

    strategy = auth_client.post("/api/strategies", json={"name": "HHB - Forex", "params": {"lookback": 7}}).get_json()

    fake_oanda_position = {
        "instrument": "EUR_USD",
        "long": {"units": "0", "unrealizedPL": "0"},
        "short": {"units": "-1000", "averagePrice": "1.0850", "unrealizedPL": "-12.50"},
    }
    monkeypatch.setattr(server.oanda_broker, "get_positions", lambda: [fake_oanda_position])

    resp = auth_client.post("/api/strategies/assign", json={"symbol": "EUR_USD", "strategy_id": strategy["id"]})
    assert resp.status_code == 200

    last_trade = state.trade_log[-1]
    assert last_trade["action"] == "buy"  # bought back the short
    assert last_trade["source"] == "strategy_switch"


# --- /webhook strategy_id validation ----------------------------------------

def test_webhook_without_strategy_id_is_not_gated_at_all(client, db_store):
    """Every alert live today (pre-migration) sends no strategy_id --
    must keep working exactly as before, unconditionally."""
    import state

    resp = _post_webhook_and_wait(client, {
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    assert resp.status_code == 202
    assert state.trade_log[-1]["symbol"] == "AAPL"


def test_webhook_with_matching_strategy_id_is_processed(client, db_store):
    import state

    strategy = db.create_strategy("HHB - Stock", {"lookback": 7})
    db.assign_strategy_to_symbol("AAPL", strategy["id"])

    resp = _post_webhook_and_wait(client, {
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
        "strategy_id": strategy["id"],
    })
    assert resp.status_code == 202
    assert state.trade_log[-1]["symbol"] == "AAPL"


def test_webhook_with_stale_strategy_id_is_rejected_and_never_reaches_the_broker(client, monkeypatch, db_store):
    import server

    old_strategy = db.create_strategy("Old", {"lookback": 5})
    new_strategy = db.create_strategy("New", {"lookback": 9})
    db.assign_strategy_to_symbol("AAPL", new_strategy["id"])  # AAPL has since been switched to `new`

    calls = []
    monkeypatch.setattr(
        server.alpaca_broker, "place_order",
        lambda symbol, side, size, order_type="market": calls.append(symbol) or {"id": "x", "status": "filled"},
    )

    resp = client.post("/webhook", json={
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
        "strategy_id": old_strategy["id"],  # a stale alert still carrying the OLD id
    })
    assert resp.status_code == 409
    assert "stale strategy_id" in resp.get_json()["error"]
    assert calls == []  # never reached the broker -- rejected synchronously, never queued


def test_webhook_with_strategy_id_but_no_assignment_at_all_is_rejected(client, db_store):
    strategy = db.create_strategy("HHB - Stock", {"lookback": 7})
    # Deliberately NOT assigning it to any symbol.
    resp = client.post("/webhook", json={
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
        "strategy_id": strategy["id"],
    })
    assert resp.status_code == 409
    assert "no active strategy assignment" in resp.get_json()["error"]


def test_webhook_strategy_id_check_fails_open_on_db_error(client, monkeypatch, db_store):
    """A DB error verifying strategy_id must NOT reject a real trade
    signal -- same 'a DB hiccup never blocks a real trade' rule applied
    everywhere else in this codebase."""
    import db as db_module
    import state

    def boom(symbol):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(db_module, "get_symbol_strategy_assignment", boom)

    resp = _post_webhook_and_wait(client, {
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
        "strategy_id": 1,
    })
    assert resp.status_code == 202
    assert state.trade_log[-1]["symbol"] == "AAPL"


def test_webhook_strategy_id_validation_stays_fast(client, db_store):
    """The synchronous strategy_id check (a DB lookup) must not
    reintroduce meaningful latency into the fast validation path --
    same rigor as the durability-write timing test."""
    strategy = db.create_strategy("HHB - Stock", {"lookback": 7})
    db.assign_strategy_to_symbol("AAPL", strategy["id"])

    start = time.monotonic()
    resp = client.post("/webhook", json={
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
        "strategy_id": strategy["id"],
    })
    elapsed = time.monotonic() - start

    assert resp.status_code == 202
    assert elapsed < 0.25
    webhook_queue.wait_for_idle("AAPL")


def test_strategy_switch_force_close_and_webhook_signal_for_same_symbol_process_in_arrival_order(
    auth_client, client, monkeypatch, db_store
):
    """Same guarantee as the safety-net/manual-close versions, for
    /api/strategies/assign's force-close-on-switch: a dashboard operator
    switching AAPL's strategy and a webhook signal for AAPL landing at
    nearly the same moment must not race each other's broker calls --
    the whole reason this route was moved onto webhook_queue too."""
    import server
    import threading

    strategy = db.create_strategy("HHB - Stock New", {"lookback": 9})

    execution_order = []
    switch_started = threading.Event()
    release_switch = threading.Event()
    call_count = {"n": 0}

    def fake_place_order(symbol, side, size, order_type="market"):
        call_count["n"] += 1
        if call_count["n"] == 1:
            execution_order.append(("strategy_switch", side))
            switch_started.set()
            assert release_switch.wait(timeout=2), "test itself timed out releasing the switch"
        else:
            execution_order.append(("webhook", side))
        return {"id": "fake-order-id", "status": "filled"}

    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="31", qty_available="31",
        avg_entry_price="200.0", current_price="210.0", unrealized_pl="310.0",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])
    monkeypatch.setattr(server.alpaca_broker, "place_order", fake_place_order)

    switch_thread = threading.Thread(
        target=lambda: auth_client.post(
            "/api/strategies/assign", json={"symbol": "AAPL", "strategy_id": strategy["id"]}
        )
    )
    switch_thread.start()
    assert switch_started.wait(timeout=2), "strategy switch never reached its (blocking) place_order call"

    webhook_resp = client.post("/webhook", json={
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    assert webhook_resp.status_code == 202

    release_switch.set()
    switch_thread.join(timeout=5)

    webhook_queue.wait_for_idle("AAPL")
    assert execution_order == [("strategy_switch", "sell"), ("webhook", "buy")]
