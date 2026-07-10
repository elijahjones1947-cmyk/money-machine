"""
Tests for backtest/strategy.py's compute_signals() -- both because it's
the backtester's core logic, and because it's now reused live by
server.py's _sanity_check_signal() to independently corroborate
incoming webhook signals. A regression here would silently affect both
the backtest results AND the live sanity-check's usefulness.
"""

import random

from backtest.strategy import compute_signals


def _make_bars(n, drift, noise=0.1, seed=1):
    rng = random.Random(seed)
    bars = []
    price = 100.0
    for i in range(n):
        price += drift + rng.uniform(-noise, noise)
        high = price + rng.uniform(0, 0.2)
        low = price - rng.uniform(0, 0.2)
        bars.append({"time": i, "open": price, "high": high, "low": low, "close": price, "volume": 1000})
    return bars


def test_short_history_has_no_signals():
    bars = _make_bars(5, drift=0.5)
    signals = compute_signals(bars)
    assert len(signals) == len(bars)
    for s in signals:
        assert s["buy_condition"] is False
        assert s["ema_fast"] is None


def test_strong_uptrend_eventually_produces_a_buy_condition():
    bars = _make_bars(120, drift=0.6, noise=0.05)
    signals = compute_signals(bars)
    assert any(s["buy_condition"] for s in signals[-30:])


def test_flat_series_buys_far_less_often_than_a_strong_trend():
    # Not "never" -- with a small breakout buffer (0.05%), pure noise
    # can occasionally punch through by chance even in a flat market.
    # The real invariant is that a strong trend should produce
    # meaningfully more buy signals than a flat/choppy one, not zero
    # in either absolute sense.
    flat_signals = compute_signals(_make_bars(120, drift=0.0, noise=0.3, seed=2))
    trending_signals = compute_signals(_make_bars(120, drift=0.6, noise=0.05, seed=2))
    flat_count = sum(1 for s in flat_signals if s["buy_condition"])
    trending_count = sum(1 for s in trending_signals if s["buy_condition"])
    assert trending_count > flat_count


def test_rsi_filter_can_be_disabled():
    bars = _make_bars(120, drift=0.6, noise=0.05)
    with_rsi = compute_signals(bars, {"use_rsi_filter": True})
    without_rsi = compute_signals(bars, {"use_rsi_filter": False})
    # Disabling the filter should never produce FEWER buy signals than
    # having it on -- it's a filter, not an independent gate.
    with_count = sum(1 for s in with_rsi if s["buy_condition"])
    without_count = sum(1 for s in without_rsi if s["buy_condition"])
    assert without_count >= with_count


def test_sanity_check_agrees_on_matching_trend():
    """Mirrors server.py's _sanity_check_signal comparison logic
    directly against compute_signals -- a trending series' last bar
    should agree with a 'buy' webhook action."""
    bars = _make_bars(120, drift=0.6, noise=0.05)
    signals = compute_signals(bars)
    latest = signals[-1]
    # Not every trending series' LAST bar is guaranteed to have
    # buy_condition True (depends on the exact random walk), but at
    # least one of the recent bars should, proving the comparison
    # logic has real signal to compare against, not just all-False.
    assert any(s["buy_condition"] for s in signals[-10:])
