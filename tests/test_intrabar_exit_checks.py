"""
Tests for server.py's run_intrabar_exit_checks() -- the polling job that
catches a TP/SL/trailing-stop level crossed INTRABAR, which TradingView's
own bar-close-only webhook exits and run_position_safety_checks() (loss-
only, every 5 min) both miss. See the function's own docstring for the
motivating incident (a HOOD intrabar spike above take-profit that pulled
back before the 30m bar closed, with no exit ever recorded).

Covers: the live peak-price tracker (state.peak_price_since_entry),
static TP/SL crossings, the trailing-stop's actual trail-then-fire
behavior (not just a static level), uniformity across stock/crypto/forex,
and the race between this job force-closing a position and a later
TradingView bar-close exit signal for the same symbol arriving anyway --
confirmed clean for all three asset classes, including forex, which
initially had no held-qty check at all (see _get_held_forex_position in
server.py, added specifically to close that gap).
"""

import logging
import time
from types import SimpleNamespace

import db
import webhook_queue


def _assign_strategy(name, take_profit_pct, stop_loss_pct, symbol, timeframe="30m"):
    strategy = db.create_strategy(
        name, {"take_profit_pct": take_profit_pct, "stop_loss_pct": stop_loss_pct}, timeframe=timeframe,
    )
    db.assign_strategy_to_symbol(symbol, strategy["id"])
    return strategy


def test_peak_price_tracker_tracks_the_high_for_a_long_and_retains_it_through_a_pullback(client, monkeypatch):
    """Isolates just the tracker's own correctness: must track the
    highest price seen (not the latest), and must NOT decrease when
    price pulls back -- that retained peak is what the trailing stop's
    trail_stop_price is computed from."""
    import server
    import state

    _assign_strategy("HHB - Stock", take_profit_pct=100.0, stop_loss_pct=100.0, symbol="AAPL")  # unreachable -- isolates the tracker from triggering an exit

    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="10",
        avg_entry_price="100.0", current_price="100.0", unrealized_pl="0.0",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])

    monkeypatch.setattr(server.alpaca_broker, "get_price", lambda symbol: 105.0)
    server.run_intrabar_exit_checks()
    assert state.peak_price_since_entry["AAPL"] == 105.0

    monkeypatch.setattr(server.alpaca_broker, "get_price", lambda symbol: 103.0)  # pulls back
    server.run_intrabar_exit_checks()
    assert state.peak_price_since_entry["AAPL"] == 105.0  # unchanged -- retains the high

    monkeypatch.setattr(server.alpaca_broker, "get_price", lambda symbol: 107.0)  # new high
    server.run_intrabar_exit_checks()
    assert state.peak_price_since_entry["AAPL"] == 107.0


def test_peak_price_tracker_tracks_the_low_for_a_short(client, monkeypatch):
    """Mirror of the above for a forex short -- the trailing stop's
    reference point is the LOWEST price for a short, not the highest."""
    import server
    import state

    _assign_strategy("HHB - Forex", take_profit_pct=100.0, stop_loss_pct=100.0, symbol="GBP_JPY")

    fake_position = {
        "instrument": "GBP_JPY",
        "long": {"units": "0", "unrealizedPL": "0"},
        "short": {"units": "-1000", "averagePrice": "190.0", "unrealizedPL": "0"},
    }
    monkeypatch.setattr(server.oanda_broker, "get_positions", lambda: [fake_position])

    monkeypatch.setattr(server.oanda_broker, "get_price", lambda symbol: 185.0)
    server.run_intrabar_exit_checks()
    assert state.peak_price_since_entry["GBP_JPY"] == 185.0

    monkeypatch.setattr(server.oanda_broker, "get_price", lambda symbol: 187.0)  # rises back partway
    server.run_intrabar_exit_checks()
    assert state.peak_price_since_entry["GBP_JPY"] == 185.0  # unchanged -- retains the low

    monkeypatch.setattr(server.oanda_broker, "get_price", lambda symbol: 183.0)  # new low
    server.run_intrabar_exit_checks()
    assert state.peak_price_since_entry["GBP_JPY"] == 183.0


def test_intrabar_poll_closes_a_long_that_crossed_take_profit(client, monkeypatch):
    import server
    import state

    _assign_strategy("HHB - Stock", take_profit_pct=1.0, stop_loss_pct=0.5, symbol="AAPL")

    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="10",
        avg_entry_price="100.0", current_price="101.5", unrealized_pl="15.0",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])
    monkeypatch.setattr(server.alpaca_broker, "get_price", lambda symbol: 101.5)  # +1.5%, TP is +1%

    server.run_intrabar_exit_checks()
    webhook_queue.wait_for_idle("AAPL")

    last_trade = state.trade_log[-1]
    assert last_trade["symbol"] == "AAPL"
    assert last_trade["action"] == "sell"
    assert last_trade["source"] == "intrabar_poll"
    assert "AAPL" not in state.peak_price_since_entry  # cleared once closed


def test_intrabar_poll_closes_a_long_that_crossed_stop_loss(client, monkeypatch):
    import server
    import state

    _assign_strategy("HHB - Stock", take_profit_pct=1.0, stop_loss_pct=0.5, symbol="AAPL")

    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="10",
        avg_entry_price="100.0", current_price="99.4", unrealized_pl="-6.0",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])
    monkeypatch.setattr(server.alpaca_broker, "get_price", lambda symbol: 99.4)  # -0.6%, SL is -0.5%

    server.run_intrabar_exit_checks()
    webhook_queue.wait_for_idle("AAPL")

    last_trade = state.trade_log[-1]
    assert last_trade["symbol"] == "AAPL"
    assert last_trade["action"] == "sell"
    assert last_trade["source"] == "intrabar_poll"


def test_intrabar_poll_does_not_close_a_position_within_normal_range(client, monkeypatch):
    """Sanity/regression check: a position that hasn't crossed anything
    must be left alone."""
    import server
    import state

    _assign_strategy("HHB - Stock", take_profit_pct=1.0, stop_loss_pct=0.5, symbol="AAPL")

    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="10",
        avg_entry_price="100.0", current_price="100.2", unrealized_pl="2.0",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])
    monkeypatch.setattr(server.alpaca_broker, "get_price", lambda symbol: 100.2)

    server.run_intrabar_exit_checks()

    assert state.trade_log == []


def test_intrabar_poll_trailing_stop_trails_the_peak_and_fires_only_past_the_offset(client, monkeypatch):
    """The core trailing-stop behavior -- NOT just a static level crossing.
    take_profit_pct=1.0, stop_loss_pct=0.5, entry=100.0:
      trail_activate_price = 100.5, trail_offset = 0.25.
    Sequence: price rises past activation (100.6, then a new high 100.8),
    a small pullback that must NOT fire (100.6 -- still above
    100.8-0.25=100.55), then a bigger pullback that DOES fire (100.5 <=
    100.55). Proves the stop trails the retained PEAK, not the entry or
    the immediately-prior price, and doesn't fire on just any pullback."""
    import server
    import state

    _assign_strategy("HHB - Stock", take_profit_pct=1.0, stop_loss_pct=0.5, symbol="AAPL")

    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="10",
        avg_entry_price="100.0", current_price="100.0", unrealized_pl="0.0",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])

    # Poll 1: 100.6 -- past trail_activate (100.5), trail_stop = 100.35 -- no hit.
    monkeypatch.setattr(server.alpaca_broker, "get_price", lambda symbol: 100.6)
    server.run_intrabar_exit_checks()
    assert state.trade_log == []
    assert state.peak_price_since_entry["AAPL"] == 100.6

    # Poll 2: new high 100.8 -- trail_stop = 100.55 -- no hit.
    monkeypatch.setattr(server.alpaca_broker, "get_price", lambda symbol: 100.8)
    server.run_intrabar_exit_checks()
    assert state.trade_log == []
    assert state.peak_price_since_entry["AAPL"] == 100.8

    # Poll 3: small pullback to 100.6 -- still above trail_stop (100.55) -- must NOT fire.
    monkeypatch.setattr(server.alpaca_broker, "get_price", lambda symbol: 100.6)
    server.run_intrabar_exit_checks()
    assert state.trade_log == []
    assert state.peak_price_since_entry["AAPL"] == 100.8  # peak still retained from poll 2

    # Poll 4: pulls back to 100.5 -- at/below trail_stop (100.55) -- fires now.
    monkeypatch.setattr(server.alpaca_broker, "get_price", lambda symbol: 100.5)
    server.run_intrabar_exit_checks()
    webhook_queue.wait_for_idle("AAPL")

    last_trade = state.trade_log[-1]
    assert last_trade["symbol"] == "AAPL"
    assert last_trade["action"] == "sell"
    assert last_trade["source"] == "intrabar_poll"
    assert "AAPL" not in state.peak_price_since_entry


def test_intrabar_poll_trailing_stop_works_for_a_short_too(client, monkeypatch):
    """Same trail-then-fire behavior, mirrored for a forex short (profit
    direction is DOWN, so the trailing stop follows the LOW and fires on
    a rise back up past the offset)."""
    import server
    import state

    _assign_strategy("HHB - Forex", take_profit_pct=1.0, stop_loss_pct=0.5, symbol="GBP_JPY")

    fake_position = {
        "instrument": "GBP_JPY",
        "long": {"units": "0", "unrealizedPL": "0"},
        "short": {"units": "-1000", "averagePrice": "190.0", "unrealizedPL": "0"},
    }
    monkeypatch.setattr(server.oanda_broker, "get_positions", lambda: [fake_position])

    # trail_activate_price = 190 * (1 - 0.5/100) = 189.05; trail_offset = 190 * 0.25/100 = 0.475
    monkeypatch.setattr(server.oanda_broker, "get_price", lambda symbol: 188.9)  # past activation, new low
    server.run_intrabar_exit_checks()
    assert state.trade_log == []
    assert state.peak_price_since_entry["GBP_JPY"] == 188.9

    monkeypatch.setattr(server.oanda_broker, "get_price", lambda symbol: 188.7)  # new, lower low
    server.run_intrabar_exit_checks()
    assert state.trade_log == []
    assert state.peak_price_since_entry["GBP_JPY"] == 188.7

    # trail_stop_price = 188.7 + 0.475 = 189.175 -- a rise to 189.0 is still below it, must NOT fire.
    monkeypatch.setattr(server.oanda_broker, "get_price", lambda symbol: 189.0)
    server.run_intrabar_exit_checks()
    assert state.trade_log == []
    assert state.peak_price_since_entry["GBP_JPY"] == 188.7

    # A rise to 189.2 is past trail_stop_price (189.175) -- fires.
    monkeypatch.setattr(server.oanda_broker, "get_price", lambda symbol: 189.2)
    server.run_intrabar_exit_checks()
    webhook_queue.wait_for_idle("GBP_JPY")

    last_trade = state.trade_log[-1]
    assert last_trade["symbol"] == "GBP_JPY"
    assert last_trade["action"] == "buy"  # closing a short
    assert last_trade["source"] == "intrabar_poll"


def test_intrabar_poll_skips_a_symbol_with_no_assigned_strategy(client, monkeypatch):
    """No take_profit_pct/stop_loss_pct to check against -- must be
    silently skipped, not raise."""
    import server
    import state

    fake_position = SimpleNamespace(
        symbol="AAPL", asset_class="us_equity", qty="10",
        avg_entry_price="100.0", current_price="500.0", unrealized_pl="4000.0",
    )
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: [fake_position])
    monkeypatch.setattr(server.alpaca_broker, "get_price", lambda symbol: 500.0)

    server.run_intrabar_exit_checks()  # must not raise

    assert state.trade_log == []


def test_intrabar_poll_a_broker_error_on_one_symbol_does_not_block_others(client, monkeypatch):
    """Same independence guarantee run_position_safety_checks() already
    has -- one symbol's get_price() failing must not prevent checking
    (or closing) the others."""
    import server
    import state

    _assign_strategy("HHB - Stock", take_profit_pct=1.0, stop_loss_pct=0.5, symbol="AAPL")
    _assign_strategy("HHB - Stock2", take_profit_pct=1.0, stop_loss_pct=0.5, symbol="HOOD")

    fake_positions = [
        SimpleNamespace(symbol="AAPL", asset_class="us_equity", qty="10",
                         avg_entry_price="100.0", current_price="100.0", unrealized_pl="0.0"),
        SimpleNamespace(symbol="HOOD", asset_class="us_equity", qty="5",
                         avg_entry_price="50.0", current_price="50.6", unrealized_pl="3.0"),
    ]
    monkeypatch.setattr(server.alpaca_broker, "get_positions", lambda: fake_positions)

    def flaky_get_price(symbol):
        if symbol == "AAPL":
            raise Exception("simulated broker error")
        return 50.6  # HOOD: +1.2%, past its 1% TP

    monkeypatch.setattr(server.alpaca_broker, "get_price", flaky_get_price)

    server.run_intrabar_exit_checks()  # must not raise despite AAPL's error
    webhook_queue.wait_for_idle("HOOD")

    last_trade = state.trade_log[-1]
    assert last_trade["symbol"] == "HOOD"
    assert last_trade["source"] == "intrabar_poll"


# --- The race: a TradingView bar-close exit signal for a symbol the
# poller ALREADY closed. Confirmed per asset class, not assumed. -------

def test_stock_webhook_exit_after_intrabar_close_cleanly_no_ops(client, monkeypatch):
    """Stock/crypto share the SAME sell-side held-qty check -- a stale
    webhook sell arriving after the poller already closed the position
    must be rejected as 'no position to sell', not double-exit."""
    import server
    import state

    _assign_strategy("HHB - Stock", take_profit_pct=1.0, stop_loss_pct=0.5, symbol="AAPL")

    position_closed = {"value": False}

    def fake_get_positions():
        if position_closed["value"]:
            return []
        return [SimpleNamespace(
            symbol="AAPL", asset_class="us_equity", qty="10",
            avg_entry_price="100.0", current_price="101.5", unrealized_pl="15.0",
        )]

    def fake_place_order(symbol, side, size, order_type="market"):
        position_closed["value"] = True
        return {"id": "fake-order-id", "status": "filled"}

    monkeypatch.setattr(server.alpaca_broker, "get_positions", fake_get_positions)
    monkeypatch.setattr(server.alpaca_broker, "get_price", lambda symbol: 101.5)
    monkeypatch.setattr(server.alpaca_broker, "place_order", fake_place_order)

    server.run_intrabar_exit_checks()
    webhook_queue.wait_for_idle("AAPL")
    assert state.trade_log[-1]["source"] == "intrabar_poll"
    trades_so_far = len(state.trade_log)

    # TradingView's own (now stale) bar-close exit signal arrives after.
    resp = client.post("/webhook", json={
        "secret": "test-webhook-secret", "action": "sell", "symbol": "AAPL",
    })
    webhook_queue.wait_for_idle("AAPL")

    assert resp.status_code == 202  # queued regardless -- rejection happens in the background
    assert len(state.trade_log) == trades_so_far  # no new trade -- cleanly rejected, not a double-exit


def test_forex_webhook_exit_after_intrabar_close_within_dedup_window_is_dropped(client, monkeypatch):
    """Within the 60s dedup window, the stale exit signal is caught as a
    duplicate of the poller's own stamp before it ever reaches the
    held-qty check below -- still a clean no-op, just via a different
    mechanism than the one the next test exercises (which covers what
    happens once that window has passed)."""
    import server
    import state

    _assign_strategy("HHB - Forex", take_profit_pct=1.0, stop_loss_pct=0.5, symbol="GBP_JPY")

    position_closed = {"value": False}

    def fake_get_positions():
        if position_closed["value"]:
            return []
        return [{
            "instrument": "GBP_JPY",
            "long": {"units": "1000", "averagePrice": "190.0", "unrealizedPL": "0"},
            "short": {"units": "0", "unrealizedPL": "0"},
        }]

    def fake_place_order(symbol, side, size, order_type="market"):
        position_closed["value"] = True
        return {"id": "fake-order-id", "status": "filled"}

    monkeypatch.setattr(server.oanda_broker, "get_positions", fake_get_positions)
    monkeypatch.setattr(server.oanda_broker, "get_price", lambda symbol: 192.0)  # +1.05%, past 1% TP
    monkeypatch.setattr(server.oanda_broker, "place_order", fake_place_order)

    server.run_intrabar_exit_checks()
    webhook_queue.wait_for_idle("GBP_JPY")
    assert state.trade_log[-1]["source"] == "intrabar_poll"
    trades_so_far = len(state.trade_log)

    resp = client.post("/webhook", json={
        "secret": "test-webhook-secret", "action": "sell", "symbol": "GBP_JPY",
    })
    webhook_queue.wait_for_idle("GBP_JPY")

    assert resp.status_code == 202
    assert len(state.trade_log) == trades_so_far  # dropped as a duplicate -- not a held-qty rejection


def test_forex_webhook_exit_after_intrabar_close_beyond_dedup_window_cleanly_no_ops(client, monkeypatch, caplog):
    """The gap this was fixed for: once the 60s dedup window has passed
    (a bar-close alert can easily arrive minutes after an intrabar poll
    fires -- polls run every 20s, bars close every 30m), the dedup drop
    above no longer protects a stale forex exit signal. Before
    _get_held_forex_position existed, this would have proceeded straight
    to sizing and placed a brand new sell order, reopening a short --
    now it's rejected the same way stock/crypto's sell-side check
    already rejects a sell with nothing to close."""
    import server
    import state

    _assign_strategy("HHB - Forex", take_profit_pct=1.0, stop_loss_pct=0.5, symbol="GBP_JPY")

    position_closed = {"value": False}
    orders = []

    def fake_get_positions():
        if position_closed["value"]:
            return []
        return [{
            "instrument": "GBP_JPY",
            "long": {"units": "1000", "averagePrice": "190.0", "unrealizedPL": "0"},
            "short": {"units": "0", "unrealizedPL": "0"},
        }]

    def fake_place_order(symbol, side, size, order_type="market"):
        orders.append((symbol, side, size))
        position_closed["value"] = True
        return {"id": "fake-order-id", "status": "filled"}

    monkeypatch.setattr(server.oanda_broker, "get_positions", fake_get_positions)
    monkeypatch.setattr(server.oanda_broker, "get_price", lambda symbol: 192.0)
    monkeypatch.setattr(server.oanda_broker, "place_order", fake_place_order)

    server.run_intrabar_exit_checks()
    webhook_queue.wait_for_idle("GBP_JPY")
    assert len(orders) == 1  # the poller's own close

    # Simulate the dedup window having already expired.
    state.last_signal_time["GBP_JPY_sell"] = time.time() - 61

    with caplog.at_level(logging.WARNING):
        resp = client.post("/webhook", json={
            "secret": "test-webhook-secret", "action": "sell", "symbol": "GBP_JPY",
        })
        webhook_queue.wait_for_idle("GBP_JPY")

    assert resp.status_code == 202
    assert len(orders) == 1  # NOT a second order -- cleanly rejected instead
    messages = [r.getMessage() for r in caplog.records]
    assert any("no long position to sell" in m for m in messages)
