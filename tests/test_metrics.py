"""
Tests for backtest/metrics.py's compute_metrics() -- used for BOTH the
strategy backtest results AND (as of this build) the live "Backtest &
live performance" page's real-trades section (server.py's
_compute_live_performance reuses this exact function so the two are
scored identically). A regression here would silently corrupt both at
once.
"""

from backtest.metrics import compute_metrics


def _trade(pnl_abs, pnl_pct, regime="trending"):
    return {"pnl_abs": pnl_abs, "pnl_pct": pnl_pct, "regime": regime}


def test_no_trades_returns_zeroed_summary():
    metrics = compute_metrics([], initial_capital=10000.0)
    assert metrics["overall"]["trade_count"] == 0
    assert metrics["overall"]["total_pnl_abs"] == 0.0
    assert metrics["overall"]["win_rate_pct"] is None
    assert metrics["by_regime"] == {}


def test_win_rate_and_total_pnl():
    trades = [_trade(100, 5.0), _trade(-50, -2.0), _trade(50, 2.5)]
    metrics = compute_metrics(trades, initial_capital=10000.0)
    overall = metrics["overall"]
    assert overall["trade_count"] == 3
    assert overall["win_rate_pct"] == round(2 / 3 * 100, 2)
    assert overall["total_pnl_abs"] == 100.0


def test_by_regime_breakdown_only_includes_regimes_present():
    trades = [
        _trade(100, 5.0, regime="trending"),
        _trade(-30, -1.5, regime="choppy"),
    ]
    metrics = compute_metrics(trades, initial_capital=10000.0)
    assert set(metrics["by_regime"].keys()) == {"trending", "choppy"}
    assert metrics["by_regime"]["trending"]["trade_count"] == 1
    assert metrics["by_regime"]["choppy"]["trade_count"] == 1
    # "volatile" never occurred in this trade set -- shouldn't appear
    assert "volatile" not in metrics["by_regime"]


def test_max_drawdown_reflects_a_losing_streak():
    # Starting at 10,000: -1000, -1000, then a partial recovery of +500
    trades = [_trade(-1000, -10.0), _trade(-1000, -11.1), _trade(500, 6.25)]
    metrics = compute_metrics(trades, initial_capital=10000.0)
    # Peak was 10,000 (the start), trough was 8,000 after both losses --
    # a 20% drawdown from peak.
    assert metrics["overall"]["max_drawdown_pct"] == 20.0


def test_sharpe_is_none_with_fewer_than_two_trades():
    metrics = compute_metrics([_trade(100, 5.0)], initial_capital=10000.0)
    assert metrics["overall"]["sharpe_ratio"] is None


def test_sharpe_is_none_when_all_returns_identical():
    # Zero variance -- Sharpe is undefined (division by zero stdev), not infinite
    trades = [_trade(100, 5.0), _trade(100, 5.0), _trade(100, 5.0)]
    metrics = compute_metrics(trades, initial_capital=10000.0)
    assert metrics["overall"]["sharpe_ratio"] is None
