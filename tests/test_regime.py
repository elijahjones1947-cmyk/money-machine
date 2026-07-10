"""
Tests for regime.py's classifier math (calculate_adx, calculate_bb_width_pct,
classify). This is the code that was silently never running for the
entire time it existed -- the scheduler job that calls it was
registered paused (next_run_time=None means "add this job PAUSED" in
APScheduler, not "no immediate run"), so market_regime stayed
permanently empty and every regime lookup fell back to "unknown".
That bug was in server.py's scheduler wiring, not in this module -- the
classification logic itself was always correct, verified when the bug
was fixed. These tests lock that in so a future regression here is
caught by a test, not by the dashboard silently going back to "unknown"
for weeks.
"""

import random

from regime import calculate_adx, calculate_bb_width_pct, classify


def _make_bars(n, drift, noise=0.15, seed=1):
    rng = random.Random(seed)
    bars = []
    price = 100.0
    for _ in range(n):
        price += drift + rng.uniform(-noise, noise)
        high = price + rng.uniform(0, 0.3)
        low = price - rng.uniform(0, 0.3)
        bars.append({"high": high, "low": low, "close": price})
    return bars


def test_adx_returns_none_with_insufficient_bars():
    assert calculate_adx(_make_bars(10, drift=0.5)) is None


def test_bb_width_returns_none_with_insufficient_bars():
    assert calculate_bb_width_pct(_make_bars(5, drift=0.0)) is None


def test_strong_trend_produces_high_adx():
    bars = _make_bars(120, drift=0.6, noise=0.1)
    adx = calculate_adx(bars)
    assert adx is not None
    assert adx > 25  # comfortably above the standard trend threshold


def test_flat_choppy_series_produces_low_adx():
    bars = _make_bars(120, drift=0.0, noise=0.5)
    adx = calculate_adx(bars)
    assert adx is not None
    assert adx < 25


def test_classify_trending_takes_priority_over_wide_bands():
    # High ADX should classify as trending regardless of band width
    assert classify(adx=40, bb_width_pct=10, thresholds={"adx_trend": 25, "bb_width_volatile": 4.0}) == "trending"


def test_classify_volatile_when_not_trending_but_wide():
    assert classify(adx=10, bb_width_pct=8, thresholds={"adx_trend": 25, "bb_width_volatile": 4.0}) == "volatile"


def test_classify_choppy_when_neither_trending_nor_wide():
    assert classify(adx=10, bb_width_pct=1, thresholds={"adx_trend": 25, "bb_width_volatile": 4.0}) == "choppy"


def test_classify_unknown_when_inputs_missing():
    assert classify(adx=None, bb_width_pct=5, thresholds={"adx_trend": 25, "bb_width_volatile": 4.0}) == "unknown"
    assert classify(adx=30, bb_width_pct=None, thresholds={"adx_trend": 25, "bb_width_volatile": 4.0}) == "unknown"


def test_end_to_end_trending_series_classifies_as_trending():
    bars = _make_bars(120, drift=0.6, noise=0.1)
    adx = calculate_adx(bars)
    bb = calculate_bb_width_pct(bars)
    result = classify(adx, bb, {"adx_trend": 25, "bb_width_volatile": 4.0})
    assert result == "trending"
