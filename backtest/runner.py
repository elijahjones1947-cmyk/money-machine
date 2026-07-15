"""
CLI entry point for running the Higher High Breakout backtest across
the bot's watched instruments, using the SAME brokers, risk config, and
regime classifier the live bot uses. Read-only — this only pulls
historical data and simulates; it never calls place_order().

Reuses config.py, so it needs the same environment variables already
set for the live app (ALPACA_*_KEY/SECRET, OANDA_*_KEY/ACCOUNT_ID,
WEBHOOK_SECRET, DASHBOARD_PASSWORD, FLASK_SECRET) — run it wherever
those are already configured (e.g. the same Railway environment, or
locally with a `.env` sourced into the shell).

Usage:
    python -m backtest.runner
    python -m backtest.runner --symbol AAPL --asset-class stock --months 6
"""

import argparse
import json

import config
from brokers.alpaca_broker import AlpacaBroker
from brokers.oanda_broker import OandaBroker
from backtest.data import fetch_bars_for_backtest
from backtest.engine import run_backtest
from backtest.regime_tagging import tag_trades_with_regime
from backtest.metrics import compute_metrics, compute_equity_curve

DEFAULT_TARGETS = [
    {"asset_class": "stock", "symbol": "AAPL", "timeframe": "30m"},
    {"asset_class": "forex", "symbol": "EUR_USD", "timeframe": "1h"},
    {"asset_class": "crypto", "symbol": "BTC/USD", "timeframe": "30m"},
]


def _build_brokers():
    alpaca_creds = config.get_broker_credentials("alpaca")
    oanda_creds = config.get_broker_credentials("oanda")
    alpaca = AlpacaBroker(
        api_key=alpaca_creds["api_key"],
        secret_key=alpaca_creds["api_secret"],
        base_url=alpaca_creds["base_url"],
    )
    oanda = OandaBroker(
        api_key=oanda_creds["api_key"],
        account_id=oanda_creds["account_id"],
        base_url=oanda_creds["base_url"],
    )
    return {"stock": alpaca, "forex": oanda, "crypto": alpaca}


def run_one(broker, symbol, asset_class, timeframe, months_back):
    bars = fetch_bars_for_backtest(broker, symbol, timeframe, months_back=months_back)
    trades = run_backtest(bars)
    tagged = tag_trades_with_regime(trades, bars, config.get_regime_config(), asset_class)
    metrics = compute_metrics(tagged)
    equity_curve = compute_equity_curve(tagged)
    return {
        "symbol": symbol,
        "asset_class": asset_class,
        "timeframe": timeframe,
        "bar_count": len(bars),
        "trades": tagged,
        "metrics": metrics,
        "equity_curve": equity_curve,
    }


def main():
    parser = argparse.ArgumentParser(description="Run the Higher High Breakout backtest.")
    parser.add_argument("--symbol", help="Single symbol to backtest (skips the default 3-instrument run)")
    parser.add_argument("--asset-class", choices=["stock", "forex", "crypto"])
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--months", type=int, default=6, help="Months of history to pull (default 6)")
    parser.add_argument("--out", default="backtest_results.json", help="Where to write full JSON results")
    args = parser.parse_args()

    brokers = _build_brokers()

    if args.symbol:
        if not args.asset_class:
            parser.error("--symbol requires --asset-class")
        targets = [{"asset_class": args.asset_class, "symbol": args.symbol, "timeframe": args.timeframe}]
    else:
        targets = DEFAULT_TARGETS

    results = []
    for t in targets:
        print("Backtesting {} ({})...".format(t["symbol"], t["asset_class"]))
        result = run_one(brokers[t["asset_class"]], t["symbol"], t["asset_class"], t["timeframe"], args.months)
        results.append(result)
        m = result["metrics"]["overall"]
        print("  {} trades | win rate {}% | max DD {}% | Sharpe {}".format(
            m["trade_count"], m["win_rate_pct"], m["max_drawdown_pct"], m["sharpe_ratio"]
        ))
        for regime_name, rm in result["metrics"]["by_regime"].items():
            print("    [{}] {} trades | win rate {}% | max DD {}%".format(
                regime_name, rm["trade_count"], rm["win_rate_pct"], rm["max_drawdown_pct"]
            ))

    with open(args.out, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("\nFull results written to {}".format(args.out))


if __name__ == "__main__":
    main()
