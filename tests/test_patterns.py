"""
Tests for patterns.py -- pure OHLC arithmetic, no broker/DB dependency.
Every fixture bar below is hand-picked so the expected pattern is
unambiguous, not just "probably detected".
"""

import patterns as p


def _bar(open_, high, low, close):
    return {"open": open_, "high": high, "low": low, "close": close}


# --- doji ------------------------------------------------------------------

def test_doji_detected_when_body_is_tiny_relative_to_range():
    bar = _bar(open_=100.0, high=101.0, low=99.0, close=100.05)  # body 0.05, range 2.0 -> 2.5%
    assert p.detect_doji(bar) is True


def test_doji_not_detected_on_a_normal_bodied_bar():
    bar = _bar(open_=100.0, high=101.0, low=99.0, close=100.8)  # body 0.8, range 2.0 -> 40%
    assert p.detect_doji(bar) is False


def test_doji_handles_zero_range_without_crashing():
    bar = _bar(open_=100.0, high=100.0, low=100.0, close=100.0)
    assert p.detect_doji(bar) is False


# --- engulfing ---------------------------------------------------------

def test_bullish_engulfing_detected():
    prev = _bar(open_=101.0, high=101.2, low=99.5, close=99.8)  # bearish
    curr = _bar(open_=99.5, high=101.5, low=99.3, close=101.3)  # bullish, engulfs prev's body
    assert p.detect_engulfing(prev, curr) == "bullish"


def test_bearish_engulfing_detected():
    prev = _bar(open_=99.8, high=101.2, low=99.5, close=101.0)  # bullish
    curr = _bar(open_=101.3, high=101.5, low=99.3, close=99.5)  # bearish, engulfs prev's body
    assert p.detect_engulfing(prev, curr) == "bearish"


def test_no_engulfing_when_current_body_does_not_contain_previous():
    prev = _bar(open_=100.0, high=100.5, low=99.5, close=99.7)
    curr = _bar(open_=99.9, high=100.3, low=99.6, close=100.1)  # smaller body, doesn't engulf
    assert p.detect_engulfing(prev, curr) is None


def test_no_engulfing_when_both_bars_are_the_same_direction():
    prev = _bar(open_=99.0, high=100.5, low=98.8, close=100.0)  # bullish
    curr = _bar(open_=98.5, high=101.0, low=98.3, close=100.8)  # also bullish, engulfs body but same direction
    assert p.detect_engulfing(prev, curr) is None


# --- pin bar / hammer / shooting star -----------------------------------

def test_bullish_pin_bar_hammer_detected():
    # small body near the top, long lower wick
    bar = _bar(open_=100.0, high=100.3, low=97.0, close=100.2)
    assert p.detect_pin_bar(bar) == "bullish"


def test_bearish_pin_bar_shooting_star_detected():
    # small body near the bottom, long upper wick
    bar = _bar(open_=100.0, high=103.0, low=99.8, close=100.1)
    assert p.detect_pin_bar(bar) == "bearish"


def test_no_pin_bar_when_body_is_too_large():
    bar = _bar(open_=100.0, high=101.5, low=98.5, close=101.5)  # body 1.5 / range 3.0 = 50%
    assert p.detect_pin_bar(bar) is None


def test_no_pin_bar_on_a_perfect_doji_zero_body():
    bar = _bar(open_=100.0, high=101.0, low=99.0, close=100.0)
    assert p.detect_pin_bar(bar) is None


# --- detect_candlestick_patterns (combines the above) -----------------

def test_detect_candlestick_patterns_returns_empty_for_no_bars():
    assert p.detect_candlestick_patterns([]) == {}


def test_detect_candlestick_patterns_checks_doji_and_pin_bar_independently():
    # This bar's body (0.2) is small enough relative to its range (3.3)
    # to qualify as BOTH a doji (<=10% body) and a pin bar (long lower
    # wick) -- confirms detect_candlestick_patterns runs both checks
    # rather than short-circuiting on the first match.
    bar = _bar(open_=100.0, high=100.3, low=97.0, close=100.2)
    result = p.detect_candlestick_patterns([bar])
    assert result.get("pin_bar") == "bullish"
    assert result.get("doji") == "neutral"


def test_detect_candlestick_patterns_includes_engulfing_with_two_bars():
    prev = _bar(open_=101.0, high=101.2, low=99.5, close=99.8)
    curr = _bar(open_=99.5, high=101.5, low=99.3, close=101.3)
    result = p.detect_candlestick_patterns([prev, curr])
    assert result["engulfing"] == "bullish"


# --- Fibonacci retracement ----------------------------------------------

def test_fibonacci_levels_are_correctly_computed():
    levels = p.fibonacci_levels(swing_high=110.0, swing_low=100.0)
    assert levels["0.0%"] == 110.0
    assert levels["100.0%"] == 100.0
    assert levels["50.0%"] == 105.0
    assert round(levels["61.8%"], 2) == round(110.0 - 0.618 * 10.0, 2)


def test_nearest_fibonacci_level_matches_price_at_50_percent():
    match = p.nearest_fibonacci_level(price=105.0, swing_high=110.0, swing_low=100.0)
    assert match is not None
    assert match[0] == "50.0%"
    assert match[1] == 105.0


def test_nearest_fibonacci_level_returns_none_when_price_is_between_levels():
    # Roughly halfway between 38.2% (106.18) and 50% (105.0), well
    # outside the default 0.5%-of-swing tolerance (0.05).
    match = p.nearest_fibonacci_level(price=105.6, swing_high=110.0, swing_low=100.0)
    assert match is None


def test_nearest_fibonacci_level_handles_zero_width_swing():
    assert p.nearest_fibonacci_level(price=100.0, swing_high=100.0, swing_low=100.0) is None


def test_detect_swing_high_low_uses_the_lookback_window():
    bars = [_bar(100 + i, 100 + i + 1, 100 + i - 1, 100 + i) for i in range(30)]
    # Highest high should come from the last bar (i=29): high=130
    high, low = p.detect_swing_high_low(bars, lookback=10)
    assert high == 129.0 + 1  # bar 29's high = 100+29+1 = 130
    assert low == 129.0 - 1 - 9  # lowest low within the last 10 bars (i=20..29): 100+20-1=119


def test_detect_swing_high_low_empty_bars_returns_none_none():
    assert p.detect_swing_high_low([]) == (None, None)


# --- analyze_patterns (the combined entry point) ------------------------

def test_analyze_patterns_combines_candlestick_and_fibonacci():
    bars = [_bar(100 + i, 100 + i + 1, 100 + i - 1, 100 + i) for i in range(19)]
    # Final bar engineered to land exactly on the 50% retracement of the
    # swing AND look like a bullish pin bar.
    swing_high = max(b["high"] for b in bars)
    swing_low = min(b["low"] for b in bars)
    fifty_pct = (swing_high + swing_low) / 2
    last_bar = _bar(open_=fifty_pct - 0.1, high=fifty_pct + 0.05, low=fifty_pct - 3.0, close=fifty_pct)
    bars = bars + [last_bar]

    result = p.analyze_patterns(bars, swing_lookback=20)
    assert "candlestick_patterns" in result
    assert "fibonacci_level" in result


def test_analyze_patterns_returns_empty_dict_for_no_data():
    assert p.analyze_patterns([]) == {}
