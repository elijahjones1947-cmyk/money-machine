"""
Candlestick + Fibonacci retracement pattern detection, purely from OHLC
bars already available via the broker APIs (broker.get_ohlcv/
get_historical_bars) -- no new dependency, hand-rolled the same way
regime.py and backtest/strategy.py compute EMA/RSI/ADX/Bollinger Bands.
TA-Lib specifically was considered and rejected: its C-extension build
step is a known pain point on Railway's buildpacks, and everything here
is simple enough OHLC arithmetic not to need it.

This is a READ-ONLY analysis module: nothing here ever gates a trade or
changes what executes, same shape as regime.py's classifier. The only
consumer today is trade_explanations.py, which folds a detected pattern
into an entry explanation's rationale when one is present.
"""


def detect_doji(bar, body_max_pct=10.0):
    """A doji: the bar's body (|close-open|) is tiny relative to its
    full range -- indecision, neither side won the bar."""
    body = abs(bar["close"] - bar["open"])
    bar_range = bar["high"] - bar["low"]
    if bar_range == 0:
        return False
    return (body / bar_range) * 100 <= body_max_pct


def detect_engulfing(prev_bar, bar):
    """Bullish engulfing: a bearish bar followed by a bullish bar whose
    body fully contains the previous bar's body (buyers overwhelmed the
    prior sellers within one bar). Bearish engulfing is the mirror.
    Returns 'bullish', 'bearish', or None."""
    prev_bullish = prev_bar["close"] > prev_bar["open"]
    curr_bullish = bar["close"] > bar["open"]

    prev_body_low = min(prev_bar["open"], prev_bar["close"])
    prev_body_high = max(prev_bar["open"], prev_bar["close"])
    curr_body_low = min(bar["open"], bar["close"])
    curr_body_high = max(bar["open"], bar["close"])

    engulfs = curr_body_low <= prev_body_low and curr_body_high >= prev_body_high
    if not engulfs:
        return None
    if curr_bullish and not prev_bullish:
        return "bullish"
    if not curr_bullish and prev_bullish:
        return "bearish"
    return None


def detect_pin_bar(bar, wick_to_body_ratio=2.0, body_max_pct=30.0):
    """A pin bar (hammer if bullish, shooting star if bearish): a small
    body with one long wick showing price was rejected from that side --
    bullish (hammer) when the long wick is on the LOW side (buyers
    stepped in and pushed price back up before the close), bearish
    (shooting star) when it's on the HIGH side. Returns 'bullish',
    'bearish', or None."""
    body = abs(bar["close"] - bar["open"])
    bar_range = bar["high"] - bar["low"]
    if bar_range == 0 or body == 0:
        return None
    if (body / bar_range) * 100 > body_max_pct:
        return None  # body too large to read as a rejection wick pattern

    upper_wick = bar["high"] - max(bar["open"], bar["close"])
    lower_wick = min(bar["open"], bar["close"]) - bar["low"]

    if lower_wick >= wick_to_body_ratio * body and lower_wick > upper_wick:
        return "bullish"
    if upper_wick >= wick_to_body_ratio * body and upper_wick > lower_wick:
        return "bearish"
    return None


def detect_candlestick_patterns(bars):
    """bars: OHLC dicts, OLDEST FIRST. Detects patterns on the LAST bar
    (engulfing also looks at the second-to-last). Returns a dict of
    whatever was found -- empty if nothing matched or there isn't enough
    data; never raises on short input."""
    if not bars:
        return {}

    patterns = {}
    last = bars[-1]

    if detect_doji(last):
        patterns["doji"] = "neutral"

    pin = detect_pin_bar(last)
    if pin:
        patterns["pin_bar"] = pin

    if len(bars) >= 2:
        engulfing = detect_engulfing(bars[-2], last)
        if engulfing:
            patterns["engulfing"] = engulfing

    return patterns


_FIB_RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)


def fibonacci_levels(swing_high, swing_low):
    """Standard retracement levels between a swing high and swing low --
    0%/100% are the swing extremes themselves, the rest are where price
    pulling back from the high would be expected to find support (or
    from the low, resistance) if the retracement holds to that ratio."""
    diff = swing_high - swing_low
    return {"{:.1f}%".format(r * 100): swing_high - r * diff for r in _FIB_RATIOS}


def nearest_fibonacci_level(price, swing_high, swing_low, tolerance_pct=0.5):
    """Returns (level_name, level_price) if `price` sits within
    tolerance_pct of the swing range's total size from one of the
    standard retracement levels, else None. tolerance_pct is scaled to
    the swing's own size (not a fixed price amount) so this works the
    same way for a $2 stock swing and a $2000 crypto swing."""
    if swing_high == swing_low:
        return None
    levels = fibonacci_levels(swing_high, swing_low)
    tolerance = abs(swing_high - swing_low) * (tolerance_pct / 100)

    best = None
    best_distance = None
    for name, level_price in levels.items():
        distance = abs(price - level_price)
        if distance <= tolerance and (best_distance is None or distance < best_distance):
            best = (name, level_price)
            best_distance = distance
    return best


def detect_swing_high_low(bars, lookback=20):
    """bars: OLDEST FIRST. Highest high / lowest low over the last
    `lookback` bars, INCLUDING the current one (unlike
    backtest.strategy.compute_signals' breakout window, which shifts
    the window to exclude the current bar -- that's a breakout-
    confirmation gate, this is just "what's the recent trading range"
    for Fibonacci context). Returns (None, None) if `bars` is empty."""
    if not bars:
        return None, None
    window = bars[-lookback:] if len(bars) >= lookback else bars
    return max(b["high"] for b in window), min(b["low"] for b in window)


def analyze_patterns(bars, swing_lookback=20, fib_tolerance_pct=0.5):
    """Full pattern read for the LAST bar in `bars` (OLDEST FIRST):
    candlestick patterns on the most recent bar(s), plus whether the
    last bar's close sits near a Fibonacci retracement level of the
    recent swing. Returns a dict -- any key absent means nothing was
    detected there, never raises on missing/short input (mirrors
    regime.py's "a classification failure only degrades logging/
    tagging, never blocks a trade" posture -- this is even lower-stakes,
    purely explanatory)."""
    result = {}

    candlesticks = detect_candlestick_patterns(bars)
    if candlesticks:
        result["candlestick_patterns"] = candlesticks

    swing_high, swing_low = detect_swing_high_low(bars, swing_lookback)
    if swing_high is not None and bars:
        fib_match = nearest_fibonacci_level(bars[-1]["close"], swing_high, swing_low, fib_tolerance_pct)
        if fib_match:
            result["fibonacci_level"] = {"name": fib_match[0], "price": fib_match[1]}

    return result
