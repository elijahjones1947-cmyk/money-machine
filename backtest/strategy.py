"""
Python port of the "Higher High Breakout" TradingView Pine Script
strategy (see the //@version=6 strategy(...) script). Same logic,
different language — EMA fast/slow trend filter, recent-high breakout
with a buffer, higher-low support confirmation, and an optional RSI
filter, plus the TP/SL and momentum-exit rules used to close positions.

Operates on plain OHLCV bar lists (oldest first) so it works
identically for stocks, forex, and crypto — the Pine script itself is
asset-agnostic; only the alert's target symbol changes on TradingView.

Defaults below match the script's input.*() defaults exactly:
  lookback=10, breakoutBuffer=0.05%, emaFast=9, emaSlow=21,
  takeProfit=0.60%, stopLoss=0.35%, rsiLength=14, rsiMin=50.
"""

DEFAULT_PARAMS = {
    "lookback": 10,               # Higher High Lookback
    "breakout_buffer_pct": 0.05,  # Breakout Buffer %
    "ema_fast_length": 9,
    "ema_slow_length": 21,
    "take_profit_pct": 0.60,
    "stop_loss_pct": 0.35,
    "use_rsi_filter": True,
    "rsi_length": 14,
    "rsi_min": 50,
}


def _ema_series(closes, length):
    """
    Standard EMA, seeded with a simple average of the first `length`
    closes then smoothed forward — matches ta.ema()'s behavior of
    having no defined value until enough bars have accumulated (those
    indices are None).
    """
    n = len(closes)
    ema = [None] * n
    if n < length:
        return ema
    k = 2 / (length + 1)
    seed = sum(closes[:length]) / length
    ema[length - 1] = seed
    for i in range(length, n):
        ema[i] = closes[i] * k + ema[i - 1] * (1 - k)
    return ema


def _rsi_series(closes, length):
    """
    Wilder's RSI, matching ta.rsi(). Seeded with a simple average of the
    first `length` gains/losses, then smoothed with Wilder's method —
    the same smoothing style already used for ADX in regime.py.
    """
    n = len(closes)
    rsi = [None] * n
    if n < length + 1:
        return rsi

    gains, losses = [], []
    for i in range(1, n):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:length]) / length
    avg_loss = sum(losses[:length]) / length

    def _rsi_from_avgs(ag, al):
        if al == 0:
            return 100.0
        rs = ag / al
        return 100 - (100 / (1 + rs))

    rsi[length] = _rsi_from_avgs(avg_gain, avg_loss)

    for i in range(length, len(gains)):
        avg_gain = (avg_gain * (length - 1) + gains[i]) / length
        avg_loss = (avg_loss * (length - 1) + losses[i]) / length
        rsi[i + 1] = _rsi_from_avgs(avg_gain, avg_loss)

    return rsi


def compute_signals(bars, params=None):
    """
    bars: list of OHLCV dicts, OLDEST FIRST (same shape as
    broker.get_ohlcv()/get_historical_bars()).
    params: overrides merged onto DEFAULT_PARAMS.

    Returns a list (same length/order as `bars`) of per-bar signal dicts:
        ema_fast, ema_slow, rsi            -- None until warmed up
        recent_high, recent_low            -- highest high / lowest low
                                               of the `lookback` bars
                                               strictly BEFORE this one
                                               (Pine's high[1]/low[1]
                                               shift excludes the
                                               current bar)
        breakout_price                     -- recent_high * (1 + buffer%)
        buy_condition (bool)                -- entry signal, evaluated
                                               on this bar's close
        sell_signal (bool)                  -- momentum-exit signal
                                               (trend flip or close <
                                               emaFast)

    Bars before enough history has accumulated get all-None/False
    signals — nothing to trade on yet, same as Pine's `na` handling.
    """
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]

    ema_fast = _ema_series(closes, p["ema_fast_length"])
    ema_slow = _ema_series(closes, p["ema_slow_length"])
    rsi = _rsi_series(closes, p["rsi_length"])

    lookback = p["lookback"]
    signals = []
    for i in range(len(bars)):
        if i < lookback:
            recent_high = recent_low = breakout_price = None
        else:
            window_highs = highs[i - lookback:i]
            window_lows = lows[i - lookback:i]
            recent_high = max(window_highs)
            recent_low = min(window_lows)
            breakout_price = recent_high * (1 + p["breakout_buffer_pct"] / 100)

        ef = ema_fast[i]
        es = ema_slow[i]
        r = rsi[i]

        trend_bullish = ef is not None and es is not None and ef > es
        trend_bearish = ef is not None and es is not None and ef < es

        higher_high_breakout = breakout_price is not None and closes[i] > breakout_price
        higher_low = recent_low is not None and lows[i] > recent_low

        rsi_ok = True if not p["use_rsi_filter"] else (r is not None and r >= p["rsi_min"])

        buy_condition = bool(higher_high_breakout and higher_low and trend_bullish and rsi_ok)
        sell_signal = bool(trend_bearish or (ef is not None and closes[i] < ef))

        signals.append({
            "ema_fast": ef,
            "ema_slow": es,
            "rsi": r,
            "recent_high": recent_high,
            "recent_low": recent_low,
            "breakout_price": breakout_price,
            "buy_condition": buy_condition,
            "sell_signal": sell_signal,
        })

    return signals
