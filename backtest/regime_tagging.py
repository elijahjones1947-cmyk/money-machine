"""
Tags each simulated trade with the market regime (trending/choppy/
volatile) it was entered in, using the SAME ADX + Bollinger Band
classifier that already tags live trades (regime.py) — run
retroactively over the historical window ending at each trade's entry,
instead of live over the most recent ~100 bars.
"""

import regime


def tag_trades_with_regime(trades, bars, regime_config, asset_class, window=100):
    """
    trades: output of backtest.engine.run_backtest()
    bars: the SAME bars list passed into run_backtest() — needed to
          look up the window of history ending at each trade's entry.
    regime_config: config.get_regime_config()-style dict keyed by
                   asset_class (thresholds regime.classify() expects).
    window: how many bars of history to feed the classifier, matching
            the ~100-bar window regime.run_regime_check() uses live.

    Returns a new list of trade dicts (originals untouched) each with
    added "regime", "adx", and "bb_width_pct" keys.
    """
    thresholds = regime_config[asset_class]
    tagged = []
    for trade in trades:
        entry_idx = trade["entry_index"]
        window_bars = bars[max(0, entry_idx - window):entry_idx]

        adx = regime.calculate_adx(window_bars)
        bb_width = regime.calculate_bb_width_pct(window_bars)
        market_regime = regime.classify(adx, bb_width, thresholds)

        tagged_trade = dict(trade)
        tagged_trade["regime"] = market_regime
        tagged_trade["adx"] = round(adx, 2) if adx is not None else None
        tagged_trade["bb_width_pct"] = round(bb_width, 4) if bb_width is not None else None
        tagged.append(tagged_trade)
    return tagged
