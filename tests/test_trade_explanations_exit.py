"""
Tests for trade_explanations.py's exit side: classify_exit_reason()
(pure, no broker dependency) and explain_exit() (the full assembly,
with a tiny fake broker for the price-action-inference path).
"""

import trade_explanations as te

PARAMS = {"take_profit_pct": 1.0, "stop_loss_pct": 0.5}


# --- classify_exit_reason: closing a LONG (action='sell') ------------------

def test_classify_take_profit_hit_exactly():
    # entry 100, TP = 101
    assert te.classify_exit_reason("sell", 100.0, 101.0, None, PARAMS) == "take_profit"


def test_classify_take_profit_hit_generously_beyond_target():
    assert te.classify_exit_reason("sell", 100.0, 105.0, None, PARAMS) == "take_profit"


def test_classify_stop_loss_when_no_trailing_activation():
    # entry 100, SL = 99.5 -- exit at 99.4, peak never reached trail-activate (100.5)
    reason = te.classify_exit_reason("sell", 100.0, 99.4, 100.2, PARAMS)
    assert reason == "stop_loss"


def test_classify_trailing_stop_after_running_past_activation_then_pulling_back():
    # trail_activate = entry * (1 + 0.5/100) = 100.5; trail_offset = entry * 0.25/100 = 0.25
    # peak reaches 100.6 (past activation), trail stop = 100.6 - 0.25 = 100.35
    reason = te.classify_exit_reason("sell", 100.0, 100.35, 100.6, PARAMS)
    assert reason == "trailing_stop"


def test_classify_momentum_exit_when_nothing_else_matches():
    # Exit above SL, never reached TP or trailing activation.
    reason = te.classify_exit_reason("sell", 100.0, 100.1, 100.2, PARAMS)
    assert reason == "momentum_exit"


def test_classify_returns_none_without_required_params():
    assert te.classify_exit_reason("sell", 100.0, 101.0, None, {}) is None


def test_classify_tolerance_forgives_small_slippage_on_stop_loss():
    # SL price is exactly 99.5 -- exit at 99.51 (0.01% off) must still count.
    reason = te.classify_exit_reason("sell", 100.0, 99.51, 100.0, PARAMS)
    assert reason == "stop_loss"


# --- classify_exit_reason: closing a SHORT (action='buy', forex only) ------

def test_classify_short_take_profit_hit_when_price_falls():
    # Short entry 100, profit direction is DOWN -- TP = 99.0
    assert te.classify_exit_reason("buy", 100.0, 98.5, None, PARAMS) == "take_profit"


def test_classify_short_stop_loss_when_price_rises():
    # SL = 100.5 -- exit at 100.6 (price moved against the short)
    reason = te.classify_exit_reason("buy", 100.0, 100.6, 99.8, PARAMS)
    assert reason == "stop_loss"


def test_classify_short_trailing_stop():
    # trail_activate = 100 - 0.5 = 99.5 (price needs to fall to activate);
    # trough reaches 99.4, trail_offset = 0.25 -> trail stop = 99.4 + 0.25 = 99.65
    reason = te.classify_exit_reason("buy", 100.0, 99.65, 99.4, PARAMS)
    assert reason == "trailing_stop"


# --- explain_exit: definitive (non-inferred) sources ------------------------

def test_explain_exit_manual_close_is_definitive_no_inference_needed():
    text = te.explain_exit("sell", "AAPL", "stock", 212.34, "manual_close")
    assert text == "Exited via manual close: operator-initiated at 212.34."


def test_explain_exit_safety_stop_loss_is_definitive():
    text = te.explain_exit("sell", "AAPL", "stock", 200.00, "safety_stop_loss")
    assert "safety-net force-close" in text
    assert "200.00" in text


def test_explain_exit_manual_trade_is_definitive():
    text = te.explain_exit("sell", "AAPL", "stock", 212.34, "manual")
    assert text == "Exited via manual trade: operator-initiated at 212.34."


# --- explain_exit: webhook (needs classification) ---------------------------

class _FakeBrokerWithBars:
    def __init__(self, bars):
        self._bars = bars

    def get_historical_bars(self, symbol, timeframe="1h", start=None, end=None):
        return self._bars


def test_explain_exit_webhook_without_entry_trade_cannot_classify():
    text = te.explain_exit("sell", "AAPL", "stock", 212.34, "webhook", entry_trade=None, params=PARAMS)
    assert "entry details unavailable" in text
    assert "couldn't be classified" in text


def test_explain_exit_webhook_take_profit():
    entry_trade = {"price": "100.00", "time": "2026-01-01T00:00:00"}
    broker = _FakeBrokerWithBars([{"high": 100.5, "low": 99.8}])
    text = te.explain_exit(
        "sell", "AAPL", "stock", 101.0, "webhook", entry_trade=entry_trade,
        params=PARAMS, broker=broker,
    )
    assert "take-profit target" in text
    assert "+1.00%" in text


def test_explain_exit_webhook_trailing_stop_mentions_percent_of_target_and_lock_in():
    entry_trade = {"price": "100.00", "time": "2026-01-01T00:00:00"}
    # Peak reached 100.6 (60% of the way to the 1.0% TP target), pulled
    # back to the trail stop (100.6 - 0.25 = 100.35).
    broker = _FakeBrokerWithBars([{"high": 100.6, "low": 99.9}])
    text = te.explain_exit(
        "sell", "AAPL", "stock", 100.35, "webhook", entry_trade=entry_trade,
        params=PARAMS, broker=broker,
    )
    assert "trailing stop" in text
    assert "% of the way to the take-profit target" in text
    assert "pulled back" in text
    assert "locking in" in text


def test_explain_exit_webhook_stop_loss():
    entry_trade = {"price": "100.00", "time": "2026-01-01T00:00:00"}
    broker = _FakeBrokerWithBars([{"high": 100.1, "low": 99.4}])
    text = te.explain_exit(
        "sell", "AAPL", "stock", 99.4, "webhook", entry_trade=entry_trade,
        params=PARAMS, broker=broker,
    )
    assert "stop-loss" in text
    assert "-0.60%" in text


def test_explain_exit_webhook_momentum_exit():
    entry_trade = {"price": "100.00", "time": "2026-01-01T00:00:00"}
    broker = _FakeBrokerWithBars([{"high": 100.2, "low": 99.9}])
    text = te.explain_exit(
        "sell", "AAPL", "stock", 100.1, "webhook", entry_trade=entry_trade,
        params=PARAMS, broker=broker,
    )
    assert "momentum/trend-flip exit" in text


def test_explain_exit_webhook_broker_failure_degrades_to_classification_without_extreme_price():
    """If fetching hold-period bars fails, classification must still
    work for take_profit/stop_loss/momentum_exit (which don't need
    extreme_price) -- only trailing_stop becomes unreachable."""
    class _BoomBroker:
        def get_historical_bars(self, *a, **k):
            raise RuntimeError("broker unreachable")

    entry_trade = {"price": "100.00", "time": "2026-01-01T00:00:00"}
    text = te.explain_exit(
        "sell", "AAPL", "stock", 101.0, "webhook", entry_trade=entry_trade,
        params=PARAMS, broker=_BoomBroker(),
    )
    assert "take-profit target" in text  # still classified correctly despite the broker failure


def test_explain_exit_webhook_missing_params_cannot_classify():
    entry_trade = {"price": "100.00", "time": "2026-01-01T00:00:00"}
    text = te.explain_exit("sell", "AAPL", "stock", 101.0, "webhook", entry_trade=entry_trade, params=None)
    assert "couldn't be classified" in text


def test_explain_exit_webhook_incomplete_params_dict_cannot_classify():
    """Distinct from params=None entirely: an assigned strategy that's
    somehow missing take_profit_pct/stop_loss_pct keys still can't
    classify, but reaches classify_exit_reason itself (returns None)
    rather than being caught by the earlier entry_trade/params-is-None
    check -- exercised separately since it's a different code path."""
    entry_trade = {"price": "100.00", "time": "2026-01-01T00:00:00"}
    text = te.explain_exit("sell", "AAPL", "stock", 101.0, "webhook", entry_trade=entry_trade, params={})
    assert "entry price or strategy params unavailable" in text
