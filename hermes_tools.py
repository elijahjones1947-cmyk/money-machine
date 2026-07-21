"""
Hermes's tool belt: read tools (portfolio/positions/trades/regime/risk/
config/market data) the agent can call freely, plus a small set of
executor tools (pause/resume trading, adjust a risk limit) that
actually change bot behavior.

Executors are intentionally NOT run the moment the model asks for them —
hermes.py stages them and makes the user confirm first (see the
EXECUTOR_TOOL_NAMES set below and hermes.py's /api/hermes/confirm route).
Read tools run immediately since they can't change anything.

Every function here takes a HermesContext (brokers + risk_manager the
live app already constructed in server.py) rather than importing server
directly — avoids a circular import (server.py registers hermes_bp,
hermes_bp's routes call these tools) and keeps this file testable on its
own.
"""

import json
import logging
import os

import config
import db
import state
import strategy_knowledge
from errors import BrokerConnectionError


class HermesContext:
    """Bundles the live broker/risk_manager instances every tool needs.
    Built once in server.py after the real brokers exist, passed into
    every tool call — see hermes.py's init_hermes()."""

    def __init__(self, alpaca_broker, oanda_broker, risk_manager):
        self.alpaca_broker = alpaca_broker
        self.oanda_broker = oanda_broker
        self.risk_manager = risk_manager
        self.brokers = {"stock": alpaca_broker, "forex": oanda_broker, "crypto": alpaca_broker}


# --- Read tools -----------------------------------------------------------

def get_portfolio_status(ctx, **_):
    """Combined account info across both brokers (stock+crypto share the
    Alpaca account; forex is OANDA)."""
    try:
        stock_acct = ctx.alpaca_broker.get_account_info()
    except BrokerConnectionError as e:
        stock_acct = {"error": str(e)}
    try:
        forex_acct = ctx.oanda_broker.get_account_info()
    except BrokerConnectionError as e:
        forex_acct = {"error": str(e)}

    combined_equity = None
    if "error" not in stock_acct and "error" not in forex_acct:
        combined_equity = round(stock_acct["equity"] + forex_acct["equity"], 2)

    return {
        "trading_mode": config.TRADING_MODE,
        "bot_enabled": state.bot_enabled,
        "combined_equity": combined_equity,
        "alpaca_account": stock_acct,
        "oanda_account": forex_acct,
    }


def get_open_positions(ctx, **_):
    """Open positions across both brokers, tagged by asset class."""
    positions = []
    try:
        for p in ctx.alpaca_broker.get_positions():
            ac = "crypto" if "/" in p.symbol else "stock"
            positions.append({
                "symbol": p.symbol, "asset_class": ac, "qty": p.qty,
                "avg_entry": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "unrealized_pl": round(float(p.unrealized_pl), 2),
            })
    except BrokerConnectionError as e:
        logging.warning("Hermes: could not fetch Alpaca positions: {}".format(e))

    try:
        for p in ctx.oanda_broker.get_positions():
            long_units = float(p.get("long", {}).get("units", 0))
            short_units = float(p.get("short", {}).get("units", 0))
            units = long_units if long_units != 0 else short_units
            avg_price = p.get("long", {}).get("averagePrice") if long_units != 0 else p.get("short", {}).get("averagePrice")
            unrealized = float(p.get("long", {}).get("unrealizedPL", 0)) + float(p.get("short", {}).get("unrealizedPL", 0))
            positions.append({
                "symbol": p["instrument"], "asset_class": "forex", "qty": units,
                "avg_entry": float(avg_price or 0), "current_price": None,
                "unrealized_pl": round(unrealized, 2),
            })
    except BrokerConnectionError as e:
        logging.warning("Hermes: could not fetch OANDA positions: {}".format(e))

    return {"positions": positions, "count": len(positions)}


def get_trade_history(ctx, limit=20, **_):
    """Recent executed trades, most recent first. Reads the same
    in-memory trade_log the dashboard shows (not a separate store)."""
    limit = min(int(limit or 20), 200)
    trades = list(reversed(state.trade_log))[:limit]
    return {"trades": trades, "returned": len(trades)}


def get_market_regime(ctx, symbol=None, **_):
    """Latest classified regime (trending/choppy/volatile) per watched
    symbol, from the same classifier + Postgres table the dashboard's
    Regime widget reads. Pass `symbol` to filter to one."""
    results = []
    for asset_class, symbols in state.watched_symbols.items():
        for sym in symbols:
            if symbol and sym != symbol:
                continue
            try:
                r = db.get_latest_regime(sym)
            except Exception:
                r = None
            results.append({
                "symbol": sym, "asset_class": asset_class,
                "regime": r["regime"] if r else "unknown",
                "adx": float(r["adx"]) if r and r.get("adx") is not None else None,
                "recorded_at": str(r["recorded_at"]) if r else None,
            })
    return {"regimes": results}


def get_recent_signals(ctx, limit=20, **_):
    """NOTE: there's no separate 'signal log' distinct from executed
    trades in this codebase yet — every entry in trade_log IS a signal
    that was acted on. Signals that got rejected (bot paused, risk
    limit, duplicate, etc.) aren't currently logged anywhere, so this
    can't show 'signals that fired but were rejected' — only ones that
    executed. Flagging as a real gap, not pretending otherwise."""
    limit = min(int(limit or 20), 200)
    trades = list(reversed(state.trade_log))[:limit]
    return {
        "signals": trades,
        "note": "Only executed signals are logged; rejected/ignored signals aren't tracked yet.",
    }


def get_risk_state(ctx, **_):
    """Snapshot of the live RiskManager: per-asset-class halt status and
    today's running P&L, plus the account-wide breaker."""
    rm = ctx.risk_manager
    return {
        "account_halted": rm.account_halted,
        "starting_equity_today": rm.starting_equity_today,
        "per_asset_class": {
            ac: {"trading_halted": rm.trading_halted[ac], "daily_pnl": round(rm.daily_pnl[ac], 2)}
            for ac in rm.asset_classes
        },
    }


def get_strategy_config(ctx, **_):
    """Current risk config, regime thresholds, and each watched symbol's
    ACTUAL currently-assigned strategy (name/version/params/timeframe),
    read live from the strategies/symbol_strategy_assignments tables
    (Phase 0/4's per-symbol strategy store) -- not a static hardcoded
    params blob. That distinction matters: a hardcoded default drifts
    the moment anyone switches a symbol to a different strategy via
    /api/strategies/assign, and it never had a timeframe/resolution
    field at all -- which previously forced a question like "what
    timeframe does entry logic run on" to be INFERRED from signal
    timestamp patterns, which is exactly how a handful of hour-aligned
    BTC/USD signals once got mistaken for a 1h timeframe when it's
    actually 30m. `timeframe` here is the confirmed fact (from the real
    TradingView alert configs), not a guess."""
    try:
        assignments = db.get_all_symbol_strategy_assignments()
    except Exception as e:
        logging.warning('get_strategy_config could not load symbol strategy assignments: {}'.format(e))
        assignments = []
    symbol_strategies = {
        a["symbol"]: {
            "strategy_id": a["id"], "name": a["name"], "version": a["version"],
            "timeframe": a["timeframe"], "params": a["params"],
        }
        for a in assignments
    }
    return {
        "risk_config": config.get_risk_config(),
        "regime_config": config.get_regime_config(),
        "symbol_strategies": symbol_strategies,
        "risk_percent": state.risk_percent,
        "max_trades_per_day": state.max_trades_per_day,
        "watched_symbols": state.watched_symbols,
    }


def get_strategy_rationale(ctx, **_):
    """The Higher High Breakout strategy's overview plus the WHY behind
    each entry rule (trend filter, breakout buffer, higher-low, RSI
    floor) and exit rule (take profit, stop loss, trailing stop,
    momentum exit) -- strategy_knowledge.py's content, distinct from
    get_strategy_config's numeric params/timeframe. Call this when asked
    to explain the strategy's psychology/reasoning, not just cite its
    numbers -- e.g. "why does it wait for a breakout instead of buying
    the dip" or "what's the point of the RSI filter" need this, not
    get_strategy_config. Static content (doesn't vary per symbol or
    strategy version -- every version of Higher High Breakout shares the
    same rule structure, just different numbers), so no DB/network call
    here, unlike get_strategy_config."""
    return strategy_knowledge.describe_strategy()


def get_asset_market_data(ctx, symbol, asset_class=None, timeframe="1h", bars=20, **_):
    """Recent OHLCV bars for a symbol, via the same broker.get_ohlcv()
    path the backtester/regime classifier use. Kept short (default 20
    bars) since this feeds an LLM context window, not a chart."""
    asset_class = asset_class or ("crypto" if "/" in symbol else "forex" if "_" in symbol else "stock")
    broker = ctx.brokers.get(asset_class)
    if broker is None:
        return {"error": "unknown asset_class: {}".format(asset_class)}
    bars = min(int(bars or 20), 200)
    try:
        data = broker.get_ohlcv(symbol, timeframe=timeframe, limit=bars)
    except BrokerConnectionError as e:
        return {"error": str(e)}
    return {
        "symbol": symbol, "asset_class": asset_class, "timeframe": timeframe,
        "bars": [{"time": str(b["time"]), "open": b["open"], "high": b["high"],
                   "low": b["low"], "close": b["close"], "volume": b["volume"]} for b in data],
    }


# Fixed proxy list for a first-cut "broad market" read — not
# comprehensive, just enough to answer "how's the market doing overall"
# without a real market-data-provider integration. Revisit if this
# needs to be more precise (real sector/index feed) later.
_BROAD_MARKET_SYMBOLS = {
    "SPY": "S&P 500", "QQQ": "Nasdaq 100", "DIA": "Dow Jones",
    "XLK": "Tech sector", "XLF": "Financials sector", "XLE": "Energy sector",
}


def get_broad_market_context(ctx, **_):
    """Best-effort snapshot of major index/sector ETFs via Alpaca, as a
    cheap proxy for 'how's the broader market doing' — not a real
    macro/sector data feed. See _BROAD_MARKET_SYMBOLS for exactly what's
    covered. Consider caching this if it gets called often (each call is
    ~6 Alpaca requests)."""
    out = []
    for sym, label in _BROAD_MARKET_SYMBOLS.items():
        try:
            bars = ctx.alpaca_broker.get_ohlcv(sym, timeframe="1d", limit=2)
            if len(bars) >= 2:
                change_pct = round((bars[-1]["close"] - bars[-2]["close"]) / bars[-2]["close"] * 100, 2)
            else:
                change_pct = None
            out.append({"symbol": sym, "label": label, "last_close": bars[-1]["close"] if bars else None, "day_change_pct": change_pct})
        except BrokerConnectionError as e:
            out.append({"symbol": sym, "label": label, "error": str(e)})
    return {"snapshot": out}


_BACKTEST_RESULTS_PATH = os.path.join(os.path.dirname(__file__), "backtest_results.json")


def get_backtest_results(ctx, symbol=None, **_):
    """Reads the same backtest_results.json the /api/backtest dashboard
    route serves (written by `python -m backtest.runner`) — lets Hermes
    answer questions like 'how did the strategy do in high-vol regimes
    last backtest' against real numbers instead of guessing. Returns
    metrics only (overall + by-regime), not the full trade list, to
    keep this cheap on tokens; pass `symbol` to filter to one instrument."""
    if not os.path.exists(_BACKTEST_RESULTS_PATH):
        return {"results": None, "note": "No backtest has been run yet (backtest_results.json doesn't exist)."}

    with open(_BACKTEST_RESULTS_PATH) as f:
        results = json.load(f)

    generated_at = None
    try:
        generated_at = str(os.path.getmtime(_BACKTEST_RESULTS_PATH))
    except OSError:
        pass

    summarized = []
    for r in results:
        if symbol and r["symbol"] != symbol:
            continue
        summarized.append({
            "symbol": r["symbol"], "asset_class": r["asset_class"],
            "timeframe": r["timeframe"], "bar_count": r["bar_count"],
            "metrics": r["metrics"],
        })

    return {"generated_at": generated_at, "results": summarized}


def get_upcoming_earnings(ctx, **_):
    """Stub — no earnings calendar data source is wired up yet (matches
    the dashboard's Earnings widget, also a stub). Needs a real
    provider before this can answer anything."""
    return {
        "earnings": [],
        "note": "No earnings calendar data source configured yet — this tool has nothing to report.",
    }


def get_daily_summary(ctx, **_):
    """Health-check + gains/losses narrative data, in one call: is the
    bot enabled, is anything halted, and what actually happened today
    (trade count, win/loss split, net P&L, broken out by symbol) plus
    yesterday's for comparison. Returns structured numbers -- Hermes
    narrates them, it doesn't compute them, so the math can't drift
    from what the dashboard shows.

    "Today"/"yesterday" are calendar days in server local time, based
    on each trade's full timestamp (state.trade_log's `time` field --
    see server.py's load_persisted_state/  _process_trade_signal for
    where that's populated as a full ISO datetime, not just a
    time-of-day, specifically so this kind of day-grouping works)."""
    import datetime as _dt

    now = _dt.datetime.now()
    today_key = now.date().isoformat()
    yesterday_key = (now.date() - _dt.timedelta(days=1)).isoformat()

    def day_key(trade):
        try:
            return _dt.datetime.fromisoformat(trade["time"]).date().isoformat()
        except (ValueError, TypeError, KeyError):
            return None

    def summarize_day(key):
        day_trades = [t for t in state.trade_log if day_key(t) == key]
        closed = [t for t in day_trades if t.get("pnl") is not None]
        wins = [t for t in closed if t["pnl"] > 0]
        losses = [t for t in closed if t["pnl"] < 0]
        by_symbol = {}
        for t in closed:
            by_symbol.setdefault(t["symbol"], 0.0)
            by_symbol[t["symbol"]] += t["pnl"]
        return {
            "trade_count": len(day_trades),
            "closed_trade_count": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "net_pnl": round(sum(t["pnl"] for t in closed), 2) if closed else 0.0,
            "pnl_by_symbol": {s: round(p, 2) for s, p in by_symbol.items()},
        }

    rm = ctx.risk_manager
    health = {
        "bot_enabled": state.bot_enabled,
        "account_halted": rm.account_halted,
        "halted_asset_classes": [ac for ac in rm.asset_classes if rm.trading_halted[ac]],
        "trading_mode": config.TRADING_MODE,
    }

    try:
        stock_acct = ctx.alpaca_broker.get_account_info()
    except BrokerConnectionError as e:
        stock_acct = {"error": str(e)}
    try:
        forex_acct = ctx.oanda_broker.get_account_info()
    except BrokerConnectionError as e:
        forex_acct = {"error": str(e)}

    combined_equity = None
    if "error" not in stock_acct and "error" not in forex_acct:
        combined_equity = round(stock_acct["equity"] + forex_acct["equity"], 2)

    equity_change_today = None
    if combined_equity is not None and rm.starting_equity_today:
        equity_change_today = round(combined_equity - rm.starting_equity_today, 2)

    return {
        "as_of": now.isoformat(),
        "health": health,
        "combined_equity": combined_equity,
        "equity_change_today": equity_change_today,
        "today": summarize_day(today_key),
        "yesterday": summarize_day(yesterday_key),
    }


# --- Executor tools (STAGED, not run immediately — see hermes.py) --------

def pause_trading(ctx, **_):
    state.bot_enabled = False
    try:
        db.save_setting("bot_enabled", False)
    except Exception as e:
        logging.warning("Hermes: could not persist bot_enabled: {}".format(e))
    return {"bot_enabled": False}


def resume_trading(ctx, **_):
    state.bot_enabled = True
    try:
        db.save_setting("bot_enabled", True)
    except Exception as e:
        logging.warning("Hermes: could not persist bot_enabled: {}".format(e))
    return {"bot_enabled": True}


def adjust_risk_limit(ctx, asset_class, risk_percent, **_):
    """Sets state.risk_percent[asset_class] — the position-SIZING knob
    (same one the dashboard's settings sliders control).

    Clamped to the RiskManager's max_position_size_pct cap for this
    asset class + mode: sizing above that cap is exactly the bug that
    silently blocked every forex trade for 2 days (the dashboard slider
    and the risk manager's validation cap could drift apart with no
    warning). Rejecting out-of-range requests here closes that gap
    rather than reproducing it through a new entry point.
    """
    if asset_class not in ("stock", "forex", "crypto"):
        return {"error": "asset_class must be stock, forex, or crypto"}

    risk_config = config.get_risk_config()
    max_allowed_pct = risk_config[asset_class]["max_position_size_pct"] * 100
    requested = float(risk_percent)

    if requested <= 0:
        return {"error": "risk_percent must be positive"}
    if requested > max_allowed_pct:
        return {
            "error": "Requested {}% exceeds the risk manager's {:.1f}% cap for {} in {} mode — rejected, not clamped, so you decide whether to change the cap itself instead.".format(
                requested, max_allowed_pct, asset_class, config.TRADING_MODE
            )
        }

    state.risk_percent[asset_class] = requested
    try:
        db.save_setting("risk_percent", state.risk_percent)
    except Exception as e:
        logging.warning("Hermes: could not persist risk_percent: {}".format(e))
    return {"asset_class": asset_class, "risk_percent": requested}


EXECUTOR_TOOL_NAMES = {"pause_trading", "resume_trading", "adjust_risk_limit"}

TOOL_FUNCTIONS = {
    "get_portfolio_status": get_portfolio_status,
    "get_open_positions": get_open_positions,
    "get_trade_history": get_trade_history,
    "get_market_regime": get_market_regime,
    "get_recent_signals": get_recent_signals,
    "get_risk_state": get_risk_state,
    "get_strategy_config": get_strategy_config,
    "get_strategy_rationale": get_strategy_rationale,
    "get_asset_market_data": get_asset_market_data,
    "get_broad_market_context": get_broad_market_context,
    "get_backtest_results": get_backtest_results,
    "get_upcoming_earnings": get_upcoming_earnings,
    "get_daily_summary": get_daily_summary,
    "pause_trading": pause_trading,
    "resume_trading": resume_trading,
    "adjust_risk_limit": adjust_risk_limit,
}

# Anthropic tool-use schemas. Kept in the same file as the implementations
# so the two can never drift out of sync with each other.
TOOL_SCHEMAS = [
    {"name": "get_portfolio_status", "description": "Get combined account/equity status across both brokers (Alpaca for stock+crypto, OANDA for forex), plus whether the bot is currently enabled.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_open_positions", "description": "Get all currently open positions across both brokers, tagged by asset class.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_trade_history", "description": "Get recent executed trades, most recent first.", "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Max trades to return (default 20, max 200)"}}}},
    {"name": "get_market_regime", "description": "Get the latest classified market regime (trending/choppy/volatile) for watched symbols.", "input_schema": {"type": "object", "properties": {"symbol": {"type": "string", "description": "Filter to one symbol (optional)"}}}},
    {"name": "get_recent_signals", "description": "Get recent trade signals that were acted on (note: only executed signals are tracked, not rejected ones).", "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    {"name": "get_risk_state", "description": "Get the live risk manager's halt status per asset class and today's running P&L.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_strategy_config", "description": "Get the current risk config, regime thresholds, watched symbols, and each symbol's actually-assigned strategy (name, version, params, and its timeframe/resolution -- e.g. 30m or 1h).", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_strategy_rationale", "description": "Get the Higher High Breakout strategy's overview and the reasoning/psychology behind each entry rule (trend filter, breakout buffer, higher-low, RSI floor) and exit rule (take profit, stop loss, trailing stop, momentum exit) -- the WHY, not just the numeric params get_strategy_config returns.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_asset_market_data", "description": "Get recent OHLCV price bars for a specific symbol.", "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}, "asset_class": {"type": "string", "enum": ["stock", "forex", "crypto"]}, "timeframe": {"type": "string", "enum": ["1m", "5m", "15m", "1h", "4h", "1d"]}, "bars": {"type": "integer"}}, "required": ["symbol"]}},
    {"name": "get_broad_market_context", "description": "Get a snapshot of major index/sector ETFs (SPY, QQQ, DIA, and key sector ETFs) as a proxy for overall market conditions.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_backtest_results", "description": "Get metrics (win rate, max drawdown, Sharpe) from the most recent backtest run, overall and broken out by market regime. Pass symbol to filter to one instrument.", "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}}},
    {"name": "get_upcoming_earnings", "description": "Get upcoming earnings dates for watched symbols. Currently returns nothing — no data source configured yet.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_daily_summary", "description": "Get a health check (bot enabled, halts, mode) plus today's and yesterday's trade counts, win/loss split, and net P&L by symbol. Use this to check the bot is functioning properly and to explain today's gains or losses with real numbers.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "pause_trading", "description": "EXECUTOR (requires user confirmation): pause the bot — blocks all new automated trades until resumed.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "resume_trading", "description": "EXECUTOR (requires user confirmation): resume the bot after a pause.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "adjust_risk_limit", "description": "EXECUTOR (requires user confirmation): change the position-sizing risk percent for an asset class. Rejected if it would exceed the risk manager's hard cap for that asset class.", "input_schema": {"type": "object", "properties": {"asset_class": {"type": "string", "enum": ["stock", "forex", "crypto"]}, "risk_percent": {"type": "number"}}, "required": ["asset_class", "risk_percent"]}},
]
