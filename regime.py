"""
Market regime classifier: tags a symbol as 'trending', 'volatile', or
'choppy' based on two independent signals computed from real price data —

  - ADX (Average Directional Index): how STRONG the current trend is,
    regardless of direction. Standard Wilder thresholds apply across all
    asset classes since ADX is already a normalized 0-100 value.
  - Bollinger Band width (as a % of price): how WIDE the recent price
    range is. This needs asset-specific thresholds, since "wide" means
    something very different for EUR/USD than for a crypto pair.

These two are complementary, not redundant: ADX asks "is this decisively
trending?" and BB width asks "how much room is it moving in right now?".
Combining them gives a richer read than either alone — see classify().
"""

import logging


def _true_range(high, low, prev_close):
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def calculate_adx(bars, period=14):
    """
    bars: list of dicts with 'high', 'low', 'close' keys, OLDEST FIRST.
    Returns the latest ADX value, or None if there isn't enough data
    (needs roughly 2x period bars for the smoothing to stabilize).
    """
    if len(bars) < period * 2:
        return None

    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]

    trs, plus_dms, minus_dms = [], [], []
    for i in range(1, len(bars)):
        trs.append(_true_range(highs[i], lows[i], closes[i - 1]))

        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)

    # Wilder's smoothing: seed with a simple sum over the first `period`
    # values, then apply the running smoothing formula for the rest.
    smoothed_tr = sum(trs[:period])
    smoothed_plus_dm = sum(plus_dms[:period])
    smoothed_minus_dm = sum(minus_dms[:period])

    dx_values = []
    for i in range(period, len(trs)):
        smoothed_tr = smoothed_tr - (smoothed_tr / period) + trs[i]
        smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dms[i]
        smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dms[i]

        if smoothed_tr == 0:
            continue

        plus_di = 100 * (smoothed_plus_dm / smoothed_tr)
        minus_di = 100 * (smoothed_minus_dm / smoothed_tr)

        di_sum = plus_di + minus_di
        dx = 100 * (abs(plus_di - minus_di) / di_sum) if di_sum != 0 else 0.0
        dx_values.append(dx)

    if len(dx_values) < period:
        return None

    # ADX = Wilder-smoothed average of DX: seed with a simple average of
    # the first `period` DX values, then smooth the rest.
    adx = sum(dx_values[:period]) / period
    for i in range(period, len(dx_values)):
        adx = ((adx * (period - 1)) + dx_values[i]) / period

    return adx


def calculate_bb_width_pct(bars, period=20, num_std=2):
    """
    bars: list of dicts with 'close', OLDEST FIRST.
    Returns Bollinger Band width as a percentage of the middle band
    (upper - lower) / middle * 100 — normalized so it's comparable
    across assets with very different absolute prices.
    """
    if len(bars) < period:
        return None

    closes = [b["close"] for b in bars[-period:]]
    middle = sum(closes) / period
    variance = sum((c - middle) ** 2 for c in closes) / period
    std = variance ** 0.5

    if middle == 0:
        return None

    upper = middle + (num_std * std)
    lower = middle - (num_std * std)
    return ((upper - lower) / middle) * 100


def classify(adx, bb_width_pct, thresholds):
    """
    thresholds = {"adx_trend": 25, "bb_width_volatile": <asset-specific>}

    Priority order:
      1. Strong trend (ADX above threshold) -> 'trending'
         A decisive trend is the most actionable state regardless of
         how wide the bands are.
      2. Not trending, but bands are wide -> 'volatile'
         Choppy AND wide-swinging — a mean-reversion trap risk, worth
         distinguishing from calm chop.
      3. Otherwise -> 'choppy'
         Low trend strength, narrow range — sideways compression.
    """
    if adx is None or bb_width_pct is None:
        return "unknown"
    if adx >= thresholds["adx_trend"]:
        return "trending"
    if bb_width_pct >= thresholds["bb_width_volatile"]:
        return "volatile"
    return "choppy"


def run_regime_check(broker, symbol, asset_class, regime_config, timeframe="1h", db_module=None):
    """
    Fetches OHLCV, computes ADX + BB width, classifies, and persists the
    result via db_module.save_regime() if provided. Returns a dict with
    the computed values regardless, so callers (e.g. the dashboard) can
    use it without hitting the DB again immediately after a fresh check.
    """
    bars = broker.get_ohlcv(symbol, timeframe=timeframe, limit=100)
    adx = calculate_adx(bars)
    bb_width = calculate_bb_width_pct(bars)

    thresholds = regime_config[asset_class]
    regime = classify(adx, bb_width, thresholds)

    result = {
        "symbol": symbol,
        "asset_class": asset_class,
        "regime": regime,
        "adx": round(adx, 2) if adx is not None else None,
        "bb_width_pct": round(bb_width, 4) if bb_width is not None else None,
    }

    if db_module is not None and regime != "unknown":
        try:
            db_module.save_regime(symbol, regime, adx=adx, volatility=bb_width)
        except Exception as e:
            logging.warning("Could not persist regime for {}: {}".format(symbol, e))

    return result
