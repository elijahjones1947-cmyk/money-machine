"""
Backtest performance metrics — win rate, max drawdown, and Sharpe ratio,
computed both across ALL trades and split out per market regime. The
per-regime breakdown is the actual payoff of tagging trades with regime
in the first place: a single flat win rate hides whether the strategy
is any good in the conditions it's most likely to actually face live.
"""

import math


def _win_rate(trades):
    if not trades:
        return None
    wins = sum(1 for t in trades if t["pnl_abs"] > 0)
    return wins / len(trades) * 100


def _max_drawdown_pct(trades, initial_capital):
    """
    Builds a running equity curve over the trade sequence (already in
    chronological order) and returns the largest peak-to-trough decline
    as a percentage of the peak.
    """
    if not trades:
        return None
    equity = initial_capital
    peak = initial_capital
    max_dd = 0.0
    for t in trades:
        equity += t["pnl_abs"]
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100)
    return max_dd


def _sharpe_ratio(trades):
    """
    Per-trade Sharpe: mean(pnl_pct) / stdev(pnl_pct), unannualized.
    Trades here are irregularly spaced in time (no fixed bars-per-year
    to annualize against), so this is comparable across regimes/runs of
    the same strategy, not a "return per year" figure.
    """
    if len(trades) < 2:
        return None
    returns = [t["pnl_pct"] for t in trades]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    stdev = math.sqrt(variance)
    if stdev == 0:
        return None
    return mean / stdev


def _summarize(trades, initial_capital):
    if not trades:
        return {
            "trade_count": 0,
            "win_rate_pct": None,
            "max_drawdown_pct": None,
            "sharpe_ratio": None,
            "total_pnl_abs": 0.0,
            "avg_pnl_pct": None,
        }
    sharpe = _sharpe_ratio(trades)
    return {
        "trade_count": len(trades),
        "win_rate_pct": round(_win_rate(trades), 2),
        "max_drawdown_pct": round(_max_drawdown_pct(trades, initial_capital), 2),
        "sharpe_ratio": round(sharpe, 4) if sharpe is not None else None,
        "total_pnl_abs": round(sum(t["pnl_abs"] for t in trades), 2),
        "avg_pnl_pct": round(sum(t["pnl_pct"] for t in trades) / len(trades), 4),
    }


def compute_equity_curve(trades, initial_capital=10000.0):
    """
    Running equity curve over a trade sequence (already in chronological
    order) — one point per trade close, plus a synthetic starting point
    at initial_capital so the curve doesn't start mid-air. This is what
    the dashboard's Backtest results page charts; kept separate from
    compute_metrics() since not every caller needs the full point series
    (e.g. summary widgets just want the scalar stats).

    Returns a list of {"time": <exit_time>, "equity": <float>} dicts.
    """
    if not trades:
        return [{"time": None, "equity": initial_capital}]

    equity = initial_capital
    curve = [{"time": trades[0]["entry_time"], "equity": equity}]
    for t in trades:
        equity += t["pnl_abs"]
        curve.append({"time": t["exit_time"], "equity": round(equity, 2)})
    return curve


def compute_metrics(tagged_trades, initial_capital=10000.0):
    """
    tagged_trades: output of regime_tagging.tag_trades_with_regime()
    (each trade dict must have a "regime" key).

    Returns:
        {
          "overall": {...},
          "by_regime": {"trending": {...}, "choppy": {...}, "volatile": {...}, ...}
        }
    Only regimes that actually occurred in this run appear in by_regime.
    """
    overall = _summarize(tagged_trades, initial_capital)

    by_regime = {}
    for r in sorted(set(t["regime"] for t in tagged_trades)):
        regime_trades = [t for t in tagged_trades if t["regime"] == r]
        by_regime[r] = _summarize(regime_trades, initial_capital)

    return {"overall": overall, "by_regime": by_regime}
