"""
Tests for server.py's Flask routes. See tests/conftest.py's module
docstring for how server.py gets imported without a real Postgres
instance or real broker network calls (a fake DB pool + fake broker
methods, not mocks of db.py's/server.py's own functions).

Covers: /api/dashboard, /api/login (valid/invalid password, failed-attempt
tracking), /webhook (valid secret, invalid secret, WEBHOOK_IP_MODE
allowlist behavior, dedup-stamp rollback on failed attempts, duplicate
drop of a genuinely successful signal, the watchlist scope gate),
/api/settings (risk_caps persistence), /api/manual_trade,
/api/manual_close, /api/watchlist (add/remove), and /api/backtest
(live_performance present).

/webhook is now ASYNC: a valid signal gets queued (webhook_queue.py) and
the route returns 202 immediately, before the actual broker calls run --
see server.py's webhook() docstring for why. Every test below that needs
to assert on a signal's REAL outcome (not just the immediate 202) uses
_post_webhook_and_wait(), which posts and then blocks until that
symbol's background queue has drained (webhook_queue.wait_for_idle) --
skipping that wait risks two things: a flaky assertion racing the
worker thread, and worse, leaking a still-running background trade into
the NEXT test's state reset. See tests/test_webhook_queue.py for
webhook_queue.py's own ordering/concurrency guarantees in isolation.
/api/manual_trade and /api/manual_close are deliberately still
SYNCHRONOUS (see webhook()'s docstring for why they weren't moved to
the async queue too), so their tests call them directly with no wait
needed.

NOT covered: /ui/layout, Hermes's routes -- share the same db plumbing
already exercised below and don't need separate fixture infrastructure,
but weren't asked for here.
"""

import json
import logging
import time
from types import SimpleNamespace

import config
import webhook_queue
from errors import BrokerConnectionError


def _post_webhook_and_wait(client, payload, **kwargs):
    """POSTs to /webhook and blocks until `payload`'s symbol has finished
    background processing -- see this module's docstring for why tests
    almost always need to wait rather than trust the immediate 202."""
    resp = client.post("/webhook", json=payload, **kwargs)
    webhook_queue.wait_for_idle(payload.get("symbol"))
    return resp


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
    import state

    resp = _post_webhook_and_wait(client, {
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    # 202 only means "queued for processing", not "trade placed" -- see
    # webhook()'s docstring. The real outcome is asserted below, after
    # _post_webhook_and_wait has confirmed the background worker ran it.
    assert resp.status_code == 202
    assert resp.get_json() == {"status": "accepted", "symbol": "AAPL", "action": "buy"}

    last_trade = state.trade_log[-1]
    assert last_trade["action"] == "buy"
    assert last_trade["symbol"] == "AAPL"
    assert last_trade["asset_class"] == "stock"


def test_webhook_entry_gets_a_fallback_explanation_when_no_bar_history(client):
    """The fake broker's default get_ohlcv() returns [] (no bars) -- the
    explanation generator must degrade gracefully to a clear fallback
    string, never raise, and never block the trade itself from logging."""
    import state

    _post_webhook_and_wait(client, {
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    explanation = state.trade_log[-1]["explanation"]
    assert explanation is not None
    assert "no rationale generated" in explanation


def test_webhook_entry_gets_a_real_explanation_with_bar_history(client, monkeypatch):
    """With real (synthetic, strongly trending) bar history available,
    the explanation must actually cite the computed indicators -- not
    just the no-data fallback."""
    import server

    def trending_bars(symbol, timeframe="1h", limit=100):
        bars = []
        price = 100.0
        for i in range(60):
            price += 0.6
            bars.append({
                "time": i, "open": price - 0.1, "high": price + 0.2, "low": price - 0.2,
                "close": price, "volume": 1000,
            })
        return bars

    monkeypatch.setattr(server.alpaca_broker, "get_ohlcv", trending_bars)

    resp = _post_webhook_and_wait(client, {
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    assert resp.status_code == 202

    import state
    explanation = state.trade_log[-1]["explanation"]
    assert explanation.startswith("Entered long:")
    assert "no rationale generated" not in explanation


def test_webhook_entry_uses_the_symbol_assigned_strategy_params(client, monkeypatch, db_store):
    """If AAPL is assigned a strategy with a custom lookback, the
    explanation (and the underlying sanity-check signal) must reflect
    THAT lookback, not the generic DEFAULT_PARAMS -- proving Phase 0's
    per-symbol store is actually being read, not just present."""
    import db
    import server

    strategy = db.create_strategy("HHB - Stock Custom", {
        "lookback": 3, "breakout_buffer_pct": 0.05, "ema_fast_length": 9, "ema_slow_length": 21,
        "take_profit_pct": 0.6, "stop_loss_pct": 0.35, "use_rsi_filter": True, "rsi_length": 14, "rsi_min": 45,
    })
    db.assign_strategy_to_symbol("AAPL", strategy["id"])

    def trending_bars(symbol, timeframe="1h", limit=100):
        bars = []
        price = 100.0
        for i in range(60):
            price += 0.6
            bars.append({
                "time": i, "open": price - 0.1, "high": price + 0.2, "low": price - 0.2,
                "close": price, "volume": 1000,
            })
        return bars

    monkeypatch.setattr(server.alpaca_broker, "get_ohlcv", trending_bars)

    _post_webhook_and_wait(client, {
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })

    import state
    explanation = state.trade_log[-1]["explanation"]
    assert "3-bar high" in explanation


def test_webhook_entry_explanation_includes_detected_candlestick_pattern(client, monkeypatch):
    """End-to-end proof that patterns.py's output actually reaches the
    final stored explanation through the real webhook -> _process_trade_
    signal path, not just unit-tested against trade_explanations.py in
    isolation."""
    import server
    import state

    def trending_bars_with_bullish_engulfing_at_the_end(symbol, timeframe="1h", limit=100):
        bars = []
        price = 100.0
        for i in range(58):
            price += 0.6
            bars.append({
                "time": i, "open": price - 0.1, "high": price + 0.2, "low": price - 0.2,
                "close": price, "volume": 1000,
            })
        # Second-to-last bar: bearish. Last bar: bullish, engulfing it.
        bars.append({"time": 58, "open": price + 0.6, "high": price + 0.7, "low": price - 0.5, "close": price - 0.4, "volume": 1000})
        bars.append({"time": 59, "open": price - 0.5, "high": price + 1.2, "low": price - 0.6, "close": price + 1.0, "volume": 1000})
        return bars

    monkeypatch.setattr(server.alpaca_broker, "get_ohlcv", trending_bars_with_bullish_engulfing_at_the_end)

    _post_webhook_and_wait(client, {
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })

    explanation = state.trade_log[-1]["explanation"]
    assert "engulfing candle" in explanation


def test_webhook_exit_gets_a_classified_explanation_end_to_end(client, monkeypatch):
    """Full round trip: buy AAPL, then sell it via a webhook exit signal,
    and confirm the resulting explanation actually classifies WHY (not
    just a generic 'exited' string) -- proving explain_exit is correctly
    wired with real entry_trade/params/broker data from a live
    _process_trade_signal call, not just unit-tested in isolation."""
    import server
    import state
    from types import SimpleNamespace

    held = {"qty": 0}

    def fake_place_order(symbol, side, size, order_type="market"):
        held["qty"] = size if side == "buy" else 0
        return {"id": "fake-order-id", "status": "filled"}

    def fake_get_positions():
        if held["qty"] > 0:
            return [SimpleNamespace(
                symbol="AAPL", asset_class="us_equity", qty=str(held["qty"]),
                avg_entry_price="100.0", current_price="100.0", unrealized_pl="0.0",
            )]
        return []

    def fake_get_price(symbol):
        return 101.0  # a clean take-profit-range fill for the sell

    def fake_get_historical_bars(symbol, timeframe="1h", start=None, end=None):
        return [{"high": 101.1, "low": 99.9}]

    monkeypatch.setattr(server.alpaca_broker, "place_order", fake_place_order)
    monkeypatch.setattr(server.alpaca_broker, "get_positions", fake_get_positions)
    monkeypatch.setattr(server.alpaca_broker, "get_historical_bars", fake_get_historical_bars)

    _post_webhook_and_wait(client, {"secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL"})

    monkeypatch.setattr(server.alpaca_broker, "get_price", fake_get_price)
    _post_webhook_and_wait(client, {"secret": "test-webhook-secret", "action": "sell", "symbol": "AAPL"})

    exit_explanation = state.trade_log[-1]["explanation"]
    assert state.trade_log[-1]["action"] == "sell"
    assert "Exited via" in exit_explanation
    assert "not yet implemented" not in exit_explanation


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
    and nothing in the logs. Both requests now return 202 regardless of
    outcome (queued, not synchronous) -- the broker failure and the
    retry's success are only observable after waiting for each to drain."""
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
    first = _post_webhook_and_wait(client, payload)
    assert first.status_code == 202

    # The failed attempt rolled back its dedup stamp...
    assert "AAPL_buy" not in state.last_signal_time

    # ...so the retry places a real order instead of 'duplicate ignored'.
    retry = _post_webhook_and_wait(client, payload)
    assert retry.status_code == 202
    assert len(calls) == 2
    assert state.trade_log[-1]["symbol"] == "AAPL"
    assert state.trade_log[-1]["action"] == "buy"


def test_webhook_rejected_sell_rolls_back_dedup_stamp(client):
    """Same rollback guarantee on a pre-broker rejection path (here: sell
    with no held position) -- not just on broker exceptions."""
    import state

    resp = _post_webhook_and_wait(client, {
        "secret": "test-webhook-secret", "action": "sell", "symbol": "AAPL",
    })
    assert resp.status_code == 202  # queued -- the rejection itself happens in the background
    assert "AAPL_sell" not in state.last_signal_time


def test_webhook_duplicate_successful_signal_rejected_and_logged(client, monkeypatch, caplog):
    """A genuinely duplicate signal (first one succeeded) within 60s must
    still be dropped -- and the drop must show up in the logs instead of
    being silent. Both requests return 202 immediately regardless of
    outcome; the dedup itself happens inside the background worker,
    which processes both strictly in the order they were queued
    (webhook_queue's own ordering guarantee -- see test_webhook_queue.py),
    so the second one reliably sees the first one's already-set dedup
    stamp by the time its turn comes up, exactly as it would have
    synchronously before this route became async."""
    import server

    calls = []

    def counting_place_order(symbol, side, size, order_type="market"):
        calls.append(symbol)
        return {"id": "fake-order-id", "status": "filled"}

    monkeypatch.setattr(server.alpaca_broker, "place_order", counting_place_order)

    payload = {"secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL"}
    with caplog.at_level(logging.INFO):
        first = client.post("/webhook", json=payload)
        dup = client.post("/webhook", json=payload)
        webhook_queue.wait_for_idle("AAPL")

    assert first.status_code == 202
    assert dup.status_code == 202
    assert any("Dropped duplicate buy stock AAPL" in r.getMessage() for r in caplog.records)

    # Only the first request ever reached the broker.
    assert calls == ["AAPL"]


def test_webhook_stamps_silence_clock_only_for_its_own_symbol(client):
    """The webhook-silence clock (state.last_webhook_at) is per SYMBOL: an
    AAPL webhook must move ONLY AAPL's timestamp, so AAPL activity can't
    mask a different symbol -- even one in the same asset class, like
    NVDA -- going silent (the audit finding). Stamped even on a
    bad-secret call -- the silence check is about whether that symbol's
    alerts are REACHING this endpoint at all."""
    import state

    resp = client.post("/webhook", json={
        "secret": "wrong", "action": "buy", "symbol": "AAPL",
    })
    assert resp.status_code == 401  # rejected -- but the symbol's clock still moves

    assert state.last_webhook_at["AAPL"] is not None
    assert state.last_webhook_at.get("NVDA") is None
    assert state.last_webhook_at.get("EUR_USD") is None
    assert state.last_webhook_at.get("BTC/USD") is None


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
    import state

    monkeypatch.setattr(config, "WEBHOOK_IP_MODE", "enforce")
    allowed_ip = next(iter(config.TRADINGVIEW_WEBHOOK_IPS))
    resp = _post_webhook_and_wait(
        client,
        {"secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL"},
        headers={"X-Forwarded-For": allowed_ip},
    )
    assert resp.status_code == 202
    assert state.trade_log[-1]["symbol"] == "AAPL"  # not blocked -- actually executed


def test_webhook_ip_allowlist_log_mode_does_not_reject(client, monkeypatch):
    import state

    monkeypatch.setattr(config, "WEBHOOK_IP_MODE", "log")
    # Same unlisted remote_addr as the enforce-mode rejection test above --
    # log mode must NOT block the trade, only (per server.py) log a warning.
    resp = _post_webhook_and_wait(client, {
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    assert resp.status_code == 202
    assert state.trade_log[-1]["symbol"] == "AAPL"  # log mode never blocks -- still executed


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


def test_get_all_positions_classifies_alpaca_crypto_by_asset_class_field(app_module, reset_state, monkeypatch):
    """Alpaca's list_positions() returns crypto symbols WITHOUT the pair
    separator ('BTCUSD'), which the slash heuristic in
    asset_class_for_symbol misreads as a stock ticker -- that exact
    misclassification sent the 5-min safety net's force-close down the
    stock order path, where Alpaca rejected it forever. The API's own
    asset_class field is authoritative, and the symbol comes back
    normalized to the slash format everything downstream speaks."""
    import server

    fake_positions = [
        SimpleNamespace(
            symbol="BTCUSD", asset_class="crypto", qty="0.235",
            avg_entry_price="63840.10", current_price="61849.93", unrealized_pl="-467.75",
        ),
        SimpleNamespace(
            symbol="AAPL", asset_class="us_equity", qty="31",
            avg_entry_price="210.00", current_price="212.50", unrealized_pl="77.50",
        ),
    ]
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: fake_positions)

    positions = {p["symbol"]: p for p in server.get_all_positions()}

    assert positions["BTC/USD"]["asset_class"] == "crypto"
    assert positions["BTC/USD"]["qty"] == 0.235
    assert positions["AAPL"]["asset_class"] == "stock"
    assert "BTCUSD" not in positions  # raw no-separator form never leaks downstream


def test_normalize_alpaca_crypto_symbol(app_module):
    import server

    assert server._normalize_alpaca_crypto_symbol("BTCUSD") == "BTC/USD"
    assert server._normalize_alpaca_crypto_symbol("ETHUSDT") == "ETH/USDT"  # longest-suffix wins, not ETHUS/DT
    assert server._normalize_alpaca_crypto_symbol("BTC/USD") == "BTC/USD"  # already normalized
    assert server._normalize_alpaca_crypto_symbol("AAPL") == "AAPL"  # unrecognized -- untouched


def test_webhook_sell_crypto_matches_alpaca_no_separator_position(client, monkeypatch):
    """A webhook sell for 'BTC/USD' must match the real position Alpaca
    reports as 'BTCUSD' -- without normalization the held-qty gate saw
    no position and wrongly rejected every crypto sell."""
    import server
    import state

    fake_position = SimpleNamespace(symbol="BTCUSD", asset_class="crypto", qty="0.235")
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])

    resp = _post_webhook_and_wait(client, {
        "secret": "test-webhook-secret", "action": "sell", "symbol": "BTC/USD",
    })
    assert resp.status_code == 202

    last_trade = state.trade_log[-1]
    assert last_trade["symbol"] == "BTC/USD"
    assert last_trade["qty"] == 0.235  # sized to the real held quantity


def test_sanity_check_sell_branch_logs_warning_instead_of_throwing(app_module, reset_state, monkeypatch, caplog):
    """The SELL branch's warning message had three {} placeholders but
    only two .format() args, so it raised IndexError on every call --
    swallowed by the catch-all and logged as 'Sanity check skipped'.
    The sell-side sanity check had never actually produced its warning."""
    import server

    monkeypatch.setattr(
        server.alpaca_broker, "get_ohlcv",
        lambda symbol, timeframe="1h", limit=100: [{}] * 50,
    )
    monkeypatch.setattr(server, "compute_signals", lambda bars, params=None: [{"sell_signal": False, "ema_fast": 123.45}])

    with caplog.at_level(logging.WARNING):
        server._sanity_check_signal(server.alpaca_broker, "AAPL", "stock", "sell")

    messages = [r.getMessage() for r in caplog.records]
    assert any("SANITY CHECK: webhook says SELL AAPL" in m for m in messages)
    assert not any("Sanity check skipped" in m for m in messages)


def test_manual_close_requires_auth(client):
    resp = client.post("/api/manual_close", json={"symbol": "AAPL"})
    assert resp.status_code == 401


def test_manual_close_missing_symbol_rejected(auth_client):
    resp = auth_client.post("/api/manual_close", json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "missing symbol"


def test_manual_close_rejects_symbol_with_no_open_position(auth_client):
    resp = auth_client.post("/api/manual_close", json={"symbol": "AAPL"})
    assert resp.status_code == 400
    assert "no open position" in resp.get_json()["error"]


def test_manual_close_blocked_when_position_already_has_a_pending_order(auth_client, monkeypatch):
    """Real incident: Alpaca's qty_available drops below qty when part of
    a position is already tied up in another open, unfilled order (e.g. a
    day order queued while the market was closed) -- a second manual
    close attempt in that state must be rejected with a clear reason
    instead of reaching Alpaca and coming back as a confusing generic
    'insufficient funds' error."""
    import server

    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="31", qty_available="0",
        avg_entry_price="320.89", current_price="328.14", unrealized_pl="224.61",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])

    resp = auth_client.post("/api/manual_close", json={"symbol": "AAPL"})
    assert resp.status_code == 400
    assert "pending" in resp.get_json()["error"]


def test_manual_close_proceeds_when_full_qty_is_available(auth_client, monkeypatch):
    """Sanity check for the guard above: when qty_available == qty (the
    normal case, nothing else pending), the close must still go through."""
    import server

    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="31", qty_available="31",
        avg_entry_price="320.89", current_price="328.14", unrealized_pl="224.61",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])

    resp = auth_client.post("/api/manual_close", json={"symbol": "AAPL"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "order placed"


def test_manual_close_sells_full_long_stock_position(auth_client, monkeypatch):
    """Closing a long stock position must SELL exactly the currently-held
    quantity (the same source of truth get_all_positions()/the dashboard's
    own positions table use), tagged with source='manual_close' so it's
    distinguishable from a strategy or safety-net exit."""
    import server
    import state

    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="31",
        avg_entry_price="210.00", current_price="212.50", unrealized_pl="77.50",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])

    resp = auth_client.post("/api/manual_close", json={"symbol": "AAPL"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "order placed"
    assert body["qty"] == 31.0

    last_trade = state.trade_log[-1]
    assert last_trade["action"] == "sell"
    assert last_trade["symbol"] == "AAPL"
    assert last_trade["source"] == "manual_close"


def test_manual_close_buys_back_a_short_forex_position(auth_client, monkeypatch):
    """Forex positions can be short (unlike stock/crypto, which are
    always long here) -- closing a short must BUY it back, not sell
    further. Mirrors run_position_safety_checks()'s own direction-aware
    close_action logic."""
    import server
    import state

    fake_oanda_position = {
        "instrument": "GBP_JPY",
        "long": {"units": "0", "unrealizedPL": "0"},
        "short": {"units": "-500", "averagePrice": "190.123", "unrealizedPL": "-12.50"},
    }
    monkeypatch.setattr(server.oanda_broker, "get_positions", lambda: [fake_oanda_position])

    resp = auth_client.post("/api/manual_close", json={"symbol": "GBP_JPY"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "order placed"
    assert body["qty"] == 500.0

    last_trade = state.trade_log[-1]
    assert last_trade["action"] == "buy"
    assert last_trade["symbol"] == "GBP_JPY"
    assert last_trade["source"] == "manual_close"


def test_manual_close_logs_a_warning_distinctly_tagged_as_manual(auth_client, monkeypatch, caplog):
    """Must land in error_log (DBLogHandler only mirrors WARNING+) with
    wording that makes clear this was an operator override, not an
    automated exit -- so discord_bot.py's Q&A bot and a human reading
    error_log later can't mistake one for the other."""
    import server

    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="31",
        avg_entry_price="210.00", current_price="212.50", unrealized_pl="77.50",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])

    with caplog.at_level(logging.WARNING):
        resp = auth_client.post("/api/manual_close", json={"symbol": "AAPL"})
    assert resp.status_code == 200

    messages = [r.getMessage() for r in caplog.records]
    assert any("MANUAL CLOSE" in m and "AAPL" in m for m in messages)


def test_manual_close_bypasses_bot_paused_and_max_trades(auth_client, monkeypatch):
    """An operator explicitly closing a position must never be blocked by
    bot_enabled=False or the daily trade cap -- same reasoning as the
    safety net's force_close_qty path, and the same bypass a strategy
    exit already gets via reduces_position."""
    import server
    import state

    state.bot_enabled = False
    state.trades_today["stock"] = state.max_trades_per_day["stock"]

    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="31",
        avg_entry_price="210.00", current_price="212.50", unrealized_pl="77.50",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])

    resp = auth_client.post("/api/manual_close", json={"symbol": "AAPL"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "order placed"


def test_webhook_rapid_buy_then_sell_same_symbol_processed_in_order(client, monkeypatch):
    """The critical correctness requirement behind moving /webhook to a
    background queue at all: a sell landing shortly after a buy for the
    SAME symbol must still be processed strictly after it, never raced
    against it. Uses a STATEFUL fake broker (place_order updates what
    get_positions reports) so this is a real end-to-end proof, not just
    an ordering check -- if the sell were processed before the buy (or
    concurrently, before the buy's fill was reflected), it would see no
    held position and get rejected with 'no position to sell' instead of
    actually closing it."""
    import server
    import state

    execution_order = []
    held_qty = {"AAPL": 0}

    def fake_place_order(symbol, side, size, order_type="market"):
        execution_order.append(side)
        held_qty[symbol] = size if side == "buy" else 0
        return {"id": "fake-order-id", "status": "filled"}

    def fake_get_positions():
        qty = held_qty.get("AAPL", 0)
        if qty > 0:
            return [SimpleNamespace(
                symbol="AAPL", asset_class="us_equity", qty=str(qty),
                avg_entry_price="100.0", current_price="100.0", unrealized_pl="0.0",
            )]
        return []

    monkeypatch.setattr(server.alpaca_broker, "place_order", fake_place_order)
    monkeypatch.setattr(server.alpaca_broker, "get_positions", fake_get_positions)

    buy_payload = {"secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL"}
    sell_payload = {"secret": "test-webhook-secret", "action": "sell", "symbol": "AAPL"}

    # Fired back-to-back on purpose -- "a sell arriving shortly after a
    # buy". Both must come back fast (queued, not executed inline) --
    # asserted separately below by the dedicated latency test, this one
    # is about ORDER, not speed.
    buy_resp = client.post("/webhook", json=buy_payload)
    sell_resp = client.post("/webhook", json=sell_payload)
    assert buy_resp.status_code == 202
    assert sell_resp.status_code == 202

    webhook_queue.wait_for_idle("AAPL")

    assert execution_order == ["buy", "sell"]
    assert state.trade_log[-2]["action"] == "buy"
    assert state.trade_log[-1]["action"] == "sell"


def test_safety_net_force_close_and_webhook_signal_for_same_symbol_process_in_arrival_order(client, monkeypatch):
    """The exact gap Step 1 closes: run_position_safety_checks() used to
    call _process_trade_signal directly, bypassing webhook_queue entirely
    -- meaning a safety-net force-close and a webhook signal for the SAME
    symbol landing at nearly the same moment could race each other's
    broker calls. Now both route through the same per-symbol queue.
    Proven with real overlap (not just back-to-back calls): the safety
    net's force-close is held mid-flight (inside its own place_order
    call) until the webhook signal has been submitted, confirming the
    webhook signal actually WAITS for the in-flight safety-net close
    rather than racing it."""
    import server
    import threading

    execution_order = []
    safety_net_started = threading.Event()
    release_safety_net = threading.Event()
    call_count = {"n": 0}

    def fake_place_order(symbol, side, size, order_type="market"):
        call_count["n"] += 1
        if call_count["n"] == 1:
            execution_order.append(("safety_net", side))
            safety_net_started.set()
            assert release_safety_net.wait(timeout=2), "test itself timed out releasing the safety net"
        else:
            execution_order.append(("webhook", side))
        return {"id": "fake-order-id", "status": "filled"}

    # cost_basis = 31 * 200 = 6200; loss of 200 -> ~3.2%, safely past the
    # 2% paper-mode stock safety_stop_loss_pct threshold (config.py).
    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="31",
        avg_entry_price="200.0", current_price="193.5", unrealized_pl="-200.0",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])
    monkeypatch.setattr(server.alpaca_broker, "place_order", fake_place_order)

    safety_net_thread = threading.Thread(target=server.run_position_safety_checks)
    safety_net_thread.start()
    assert safety_net_started.wait(timeout=2), "safety net never reached its (blocking) place_order call"

    # The AAPL worker thread is now BUSY (blocked inside the safety net's
    # place_order) -- this webhook signal must queue behind it, not race it.
    webhook_resp = client.post("/webhook", json={
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    assert webhook_resp.status_code == 202  # accepted/queued immediately regardless

    release_safety_net.set()
    safety_net_thread.join(timeout=5)
    webhook_queue.wait_for_idle("AAPL")

    assert execution_order == [("safety_net", "sell"), ("webhook", "buy")]


def test_manual_close_and_webhook_signal_for_same_symbol_process_in_arrival_order(client, monkeypatch):
    """Same guarantee, for /api/manual_close: a dashboard operator
    clicking Close and a webhook signal for the same symbol landing at
    nearly the same moment must not race each other's broker calls."""
    import server
    import threading

    execution_order = []
    manual_close_started = threading.Event()
    release_manual_close = threading.Event()
    call_count = {"n": 0}

    def fake_place_order(symbol, side, size, order_type="market"):
        call_count["n"] += 1
        if call_count["n"] == 1:
            execution_order.append(("manual_close", side))
            manual_close_started.set()
            assert release_manual_close.wait(timeout=2), "test itself timed out releasing the manual close"
        else:
            execution_order.append(("webhook", side))
        return {"id": "fake-order-id", "status": "filled"}

    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="31", qty_available="31",
        avg_entry_price="200.0", current_price="210.0", unrealized_pl="310.0",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])
    monkeypatch.setattr(server.alpaca_broker, "place_order", fake_place_order)

    # Plain (non-context-manager) test client -- `with app.test_client()`
    # ties Flask's request context to the thread that ENTERS the block,
    # which breaks when the actual request runs on a different thread
    # (as it must here, to get real overlap with the webhook POST below).
    manual_close_client = server.app.test_client()
    with manual_close_client.session_transaction() as sess:
        sess["auth"] = True

    manual_close_thread = threading.Thread(
        target=lambda: manual_close_client.post("/api/manual_close", json={"symbol": "AAPL"})
    )
    manual_close_thread.start()
    assert manual_close_started.wait(timeout=2), "manual close never reached its (blocking) place_order call"

    webhook_resp = client.post("/webhook", json={
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    assert webhook_resp.status_code == 202

    release_manual_close.set()
    manual_close_thread.join(timeout=5)

    webhook_queue.wait_for_idle("AAPL")
    assert execution_order == [("manual_close", "sell"), ("webhook", "buy")]


def test_webhook_responds_fast_even_when_a_broker_call_is_slow(client, monkeypatch):
    """The whole point of webhook_queue: TradingView must get its 202
    back well within its own delivery timeout regardless of how slow the
    broker round-trip chain turns out to be. Reproduces the real
    incident (confirmed via Railway's HTTP-level response times: two
    genuine TradingView delivery timeouts at ~2.7-2.85s, both of which
    had ALREADY placed their order server-side by the time TradingView
    gave up waiting) by making get_account_info artificially slow, then
    asserting the HTTP response comes back almost immediately regardless."""
    import server

    def slow_get_account_info():
        time.sleep(0.5)
        return {"equity": 10000.0, "buying_power": 10000.0, "last_equity": 10000.0}

    monkeypatch.setattr(server.alpaca_broker, "get_account_info", slow_get_account_info)

    start = time.monotonic()
    resp = client.post("/webhook", json={
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    elapsed = time.monotonic() - start

    assert resp.status_code == 202
    assert elapsed < 0.25  # comfortably under the 0.5s the slow broker call alone takes

    webhook_queue.wait_for_idle("AAPL")  # let it finish before the test ends


def test_webhook_auth_failure_still_returns_synchronously_and_fast(client):
    """No regression on the one thing that was explicitly NOT supposed to
    change: validation/auth failures must still fail fast and
    synchronously, never touching webhook_queue at all. Confirmed two
    ways -- timing (comfortably faster than any broker round-trip could
    be) and that no worker thread/queue ever gets created for the
    symbol, proving the call never reached enqueue()."""
    symbol = "ZZZZ_NEVER_QUEUED_AUTH_TEST"

    start = time.monotonic()
    resp = client.post("/webhook", json={
        "secret": "wrong", "action": "buy", "symbol": symbol,
    })
    elapsed = time.monotonic() - start

    assert resp.status_code == 401
    assert elapsed < 0.1
    assert symbol not in webhook_queue._queues


def test_webhook_persists_durably_before_responding(client, db_store):
    """The actual durability guarantee: by the time /webhook returns 202,
    the signal must already be a real row in webhook_signals -- not just
    sitting in the in-memory queue -- since that row (db.py) is what
    survives a process kill/restart, not webhook_queue.py's in-memory
    structures."""
    resp = client.post("/webhook", json={
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    assert resp.status_code == 202

    assert len(db_store["webhook_signals"]) == 1
    row = db_store["webhook_signals"][0]
    assert row["symbol"] == "AAPL"
    assert row["action"] == "buy"
    # Status may already have advanced past 'pending' by the time we
    # check (this assertion races the background worker) -- what matters
    # here is that the row EXISTS, synchronously, before the response.
    assert row["status"] in ("pending", "processing", "done")

    webhook_queue.wait_for_idle("AAPL")


def test_webhook_signal_row_marked_done_after_successful_processing(client, db_store):
    resp = _post_webhook_and_wait(client, {
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    assert resp.status_code == 202
    assert db_store["webhook_signals"][0]["status"] == "done"


def test_webhook_signal_row_marked_done_even_when_trade_is_rejected(client, db_store):
    """A REJECTED trade (e.g. no position to sell) is still a normally
    COMPLETED signal from the durable queue's point of view -- 'done'
    means "ran to completion", not "the trade was accepted". Only a
    genuinely unexpected exception should ever produce 'failed'."""
    resp = _post_webhook_and_wait(client, {
        "secret": "test-webhook-secret", "action": "sell", "symbol": "AAPL",
    })
    assert resp.status_code == 202
    assert db_store["webhook_signals"][0]["status"] == "done"


def test_webhook_signal_row_marked_failed_on_unexpected_exception(client, monkeypatch, db_store):
    import server

    def boom(*a, **k):
        raise RuntimeError("simulated unexpected bug")

    monkeypatch.setattr(server, "_process_trade_signal", boom)

    resp = _post_webhook_and_wait(client, {
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    assert resp.status_code == 202
    row = db_store["webhook_signals"][0]
    assert row["status"] == "failed"
    assert "simulated unexpected bug" in row["error_message"]


def test_webhook_falls_back_to_in_memory_only_if_db_persist_fails(client, monkeypatch, caplog):
    """A transient DB outage must never be the reason a real trade signal
    gets rejected outright -- same 'a DB hiccup never blocks a real
    trade' rule this codebase applies everywhere else (e.g. save_trade).
    The signal still gets queued and processed, just without a durable
    row backing it for this one instance."""
    import db as db_module
    import state

    def boom(*a, **k):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(db_module, "enqueue_webhook_signal", boom)

    with caplog.at_level(logging.ERROR):
        resp = _post_webhook_and_wait(client, {
            "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
        })

    assert resp.status_code == 202
    assert state.trade_log[-1]["symbol"] == "AAPL"  # still processed despite the DB failure
    assert any("durability degraded" in r.getMessage() for r in caplog.records)


def test_webhook_synchronous_db_write_stays_fast_even_with_slow_broker(client, monkeypatch):
    """The new durable-write-before-responding step must not reintroduce
    the exact timeout problem this whole feature exists to fix: the
    persist-then-ack path stays fast regardless of how slow the
    (backgrounded) broker call turns out to be."""
    import server

    def slow_get_account_info():
        time.sleep(0.5)
        return {"equity": 10000.0, "buying_power": 10000.0, "last_equity": 10000.0}

    monkeypatch.setattr(server.alpaca_broker, "get_account_info", slow_get_account_info)

    start = time.monotonic()
    resp = client.post("/webhook", json={
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    elapsed = time.monotonic() - start

    assert resp.status_code == 202
    assert elapsed < 0.25  # comfortably under the 0.5s the slow broker call alone takes

    webhook_queue.wait_for_idle("AAPL")


def test_recover_pending_webhook_signals_resumes_leftover_pending_rows(client, db_store, monkeypatch):
    """Simulates a crash: pending rows already sit in webhook_signals (as
    if /webhook had accepted and persisted them, then the process died
    before executing either) -- recover_pending_webhook_signals() must
    pick them up and actually execute them through the normal path."""
    import server
    import state

    execution_order = []

    def recording_place_order(symbol, side, size, order_type="market"):
        execution_order.append((symbol, side))
        return {"id": "fake-order-id", "status": "filled"}

    monkeypatch.setattr(server.alpaca_broker, "place_order", recording_place_order)

    db_store["webhook_signals"].append({
        "id": 101, "symbol": "AAPL", "action": "buy", "manual_flag": False,
        "status": "pending", "received_at": None, "completed_at": None, "error_message": None,
    })
    db_store["webhook_signals"].append({
        "id": 102, "symbol": "HOOD", "action": "buy", "manual_flag": False,
        "status": "pending", "received_at": None, "completed_at": None, "error_message": None,
    })

    server.recover_pending_webhook_signals()
    webhook_queue.wait_for_idle("AAPL")
    webhook_queue.wait_for_idle("HOOD")

    assert ("AAPL", "buy") in execution_order
    assert ("HOOD", "buy") in execution_order

    statuses = {row["id"]: row["status"] for row in db_store["webhook_signals"]}
    assert statuses[101] == "done"
    assert statuses[102] == "done"


def test_recover_pending_webhook_signals_preserves_per_symbol_order(client, db_store, monkeypatch):
    """Two pending AAPL signals (buy then sell) left over from a crash
    must resume in the SAME order they were originally received, never
    raced or reordered -- this is the entire reason recovery re-enqueues
    through webhook_queue's per-symbol FIFO mechanism instead of just
    firing all pending rows arbitrarily. Uses the same stateful-fake-
    broker proof as the live rapid-buy-then-sell test: the sell only
    succeeds if it actually sees the buy's fill, so a real ordering
    violation would show up as a false rejection, not just a re-sorted list."""
    import server
    import state

    execution_order = []
    held_qty = {"AAPL": 0}

    def fake_place_order(symbol, side, size, order_type="market"):
        execution_order.append(side)
        held_qty[symbol] = size if side == "buy" else 0
        return {"id": "fake-order-id", "status": "filled"}

    def fake_get_positions():
        qty = held_qty.get("AAPL", 0)
        if qty > 0:
            return [SimpleNamespace(
                symbol="AAPL", asset_class="us_equity", qty=str(qty),
                avg_entry_price="100.0", current_price="100.0", unrealized_pl="0.0",
            )]
        return []

    monkeypatch.setattr(server.alpaca_broker, "place_order", fake_place_order)
    monkeypatch.setattr(server.alpaca_broker, "get_positions", fake_get_positions)

    db_store["webhook_signals"].append({
        "id": 201, "symbol": "AAPL", "action": "buy", "manual_flag": False,
        "status": "pending", "received_at": None, "completed_at": None, "error_message": None,
    })
    db_store["webhook_signals"].append({
        "id": 202, "symbol": "AAPL", "action": "sell", "manual_flag": False,
        "status": "pending", "received_at": None, "completed_at": None, "error_message": None,
    })

    server.recover_pending_webhook_signals()
    webhook_queue.wait_for_idle("AAPL")

    assert execution_order == ["buy", "sell"]
    assert "error" not in state.trade_log[-1]  # sell actually saw the buy's position


def test_recover_pending_webhook_signals_does_not_resume_stuck_rows(client, db_store, monkeypatch, caplog):
    """A row stuck in 'processing' (crash mid-execution) or 'failed' (an
    unexpected exception) must NEVER be auto-resumed -- there's no safe
    way to tell whether the broker call already fired, and blindly
    retrying risks a DUPLICATE real order. These get logged loudly
    instead, for manual review."""
    import server

    calls = []
    monkeypatch.setattr(
        server.alpaca_broker, "place_order",
        lambda symbol, side, size, order_type="market": calls.append(symbol) or {"id": "x", "status": "filled"},
    )

    db_store["webhook_signals"].append({
        "id": 301, "symbol": "AAPL", "action": "buy", "manual_flag": False,
        "status": "processing", "received_at": None, "completed_at": None, "error_message": None,
    })
    db_store["webhook_signals"].append({
        "id": 302, "symbol": "MSFT", "action": "buy", "manual_flag": False,
        "status": "failed", "received_at": None, "completed_at": None, "error_message": "simulated crash",
    })

    with caplog.at_level(logging.ERROR):
        server.recover_pending_webhook_signals()

    assert calls == []  # neither one ever reached the broker
    messages = [r.getMessage() for r in caplog.records]
    assert any("#301" in m and "NOT auto-resumed" in m for m in messages)
    assert any("#302" in m and "NOT auto-resumed" in m for m in messages)


def test_backtest_response_includes_live_performance_key(auth_client):
    resp = auth_client.get("/api/backtest")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "live_performance" in body
    # state.trade_log is empty (reset_state), so this is the no-closed-trades shape.
    assert body["live_performance"]["trade_count"] == 0


# --- Watchlist scope gate (Step 4: limiting live trading to 2 symbols
# per asset class) --------------------------------------------------

def test_webhook_rejects_new_entry_for_symbol_not_on_watchlist(client, caplog):
    """MSFT was dropped from the default watchlist (state.py) -- a
    webhook signal for it, with the bot holding no position, must be
    rejected outright rather than silently opening a new position the
    watchlist no longer includes. /webhook always responds 202
    immediately regardless of outcome (see webhook()'s docstring) -- the
    actual rejection happens in the background, observable via
    trade_log staying empty and the logged warning."""
    import state

    with caplog.at_level(logging.WARNING):
        resp = _post_webhook_and_wait(client, {
            "secret": "test-webhook-secret", "action": "buy", "symbol": "MSFT",
        })
    assert resp.status_code == 202
    assert state.trade_log == []
    messages = [r.getMessage() for r in caplog.records]
    assert any("not on the watchlist" in m and "MSFT" in m for m in messages)


def test_webhook_allows_signal_for_dropped_symbol_the_bot_still_holds(client, monkeypatch):
    """A symbol dropped from the watchlist that the bot ALREADY holds a
    position in (e.g. from before it was dropped) must keep receiving
    signals normally -- otherwise it would be silently stranded open
    forever with no way to exit via its own strategy's exit alert."""
    import server
    import state

    fake_position = SimpleNamespace(
        symbol="MSFT", asset_class="us_equity", qty="25", qty_available="25",
        avg_entry_price="399.09", current_price="399.83", unrealized_pl="18.45",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])

    resp = _post_webhook_and_wait(client, {
        "secret": "test-webhook-secret", "action": "sell", "symbol": "MSFT",
    })
    assert resp.status_code == 202
    last_trade = state.trade_log[-1]
    assert last_trade["symbol"] == "MSFT"
    assert last_trade["action"] == "sell"


def test_webhook_watchlist_gate_does_not_apply_to_still_watched_symbols(client):
    """Sanity check: the gate must not accidentally start rejecting
    signals for symbols that ARE still on the watchlist (AAPL)."""
    import state

    resp = _post_webhook_and_wait(client, {
        "secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL",
    })
    assert resp.status_code == 202
    assert state.trade_log[-1]["symbol"] == "AAPL"


def test_manual_trade_bypasses_watchlist_gate(auth_client):
    """An operator explicitly using the dashboard's manual buy/sell
    buttons must never be blocked by the watchlist scope -- same
    exemption bot_enabled/max_trades_per_day already get for is_manual."""
    import state

    resp = auth_client.post("/api/manual_trade", json={"action": "buy", "symbol": "MSFT"})
    assert resp.status_code == 200
    assert state.trade_log[-1]["symbol"] == "MSFT"
    assert state.trade_log[-1]["source"] == "manual"


def test_safety_net_force_close_bypasses_watchlist_gate(app_module, reset_state, monkeypatch):
    """The safety net force-closing a losing position must never be
    blocked by the watchlist scope, even for a symbol that's since been
    dropped -- an emergency exit must always be allowed through."""
    import server
    import state

    # cost_basis = 25 * 400 = 10000; loss of 400 -> 4%, past the 2% paper
    # stock safety_stop_loss_pct threshold (config.py).
    fake_position = SimpleNamespace(
        symbol="MSFT", asset_class="us_equity", qty="25",
        avg_entry_price="400.0", current_price="384.0", unrealized_pl="-400.0",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])

    server.run_position_safety_checks()

    last_trade = state.trade_log[-1]
    assert last_trade["symbol"] == "MSFT"
    assert last_trade["action"] == "sell"
    assert last_trade["source"] == "safety_stop_loss"


def test_remove_watchlist_requires_auth(client):
    resp = client.delete("/api/watchlist", json={"symbol": "MSFT"})
    assert resp.status_code == 401


def test_add_then_remove_watchlist_round_trip(auth_client):
    import state

    assert "TSLA" not in state.watched_symbols["stock"]
    add_resp = auth_client.post("/api/watchlist", json={"symbol": "TSLA", "asset_class": "stock"})
    assert add_resp.status_code == 200
    assert add_resp.get_json() == {"status": "added"}
    assert "TSLA" in state.watched_symbols["stock"]

    remove_resp = auth_client.delete("/api/watchlist", json={"symbol": "TSLA", "asset_class": "stock"})
    assert remove_resp.status_code == 200
    assert remove_resp.get_json() == {"status": "removed"}
    assert "TSLA" not in state.watched_symbols["stock"]


def test_remove_watchlist_symbol_not_present_is_a_no_op(auth_client):
    resp = auth_client.delete("/api/watchlist", json={"symbol": "ZZZZ", "asset_class": "stock"})
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "not present"}


def test_remove_watchlist_infers_asset_class_when_omitted(auth_client):
    import state

    assert "BTC/USD" in state.watched_symbols["crypto"]
    resp = auth_client.delete("/api/watchlist", json={"symbol": "BTC/USD"})
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "removed"}
    assert "BTC/USD" not in state.watched_symbols["crypto"]
