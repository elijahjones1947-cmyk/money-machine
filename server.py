from flask import Flask, request, jsonify, session, send_from_directory
import logging, time, datetime, math, copy, hmac, traceback
import concurrent.futures

import config
import state
import db
import regime
import alerts
import webhook_queue
import trade_explanations
import patterns
from apscheduler.schedulers.background import BackgroundScheduler
from errors import InsufficientFundsError, MarketClosedError, InvalidSymbolError, BrokerConnectionError
from brokers.alpaca_broker import AlpacaBroker
from brokers.oanda_broker import OandaBroker
from risk.risk_manager import RiskManager
from hermes import hermes_bp, init_hermes
from backtest.metrics import compute_metrics
from backtest.strategy import compute_signals, DEFAULT_PARAMS as STRATEGY_DEFAULT_PARAMS
import json
import os

# static_folder=None: Flask's own auto-registered static route uses
# the exact same '/<path:...>' pattern our SPA catch-all needs below,
# and since it's registered first (at app construction), it was
# winning every match and 404ing anything that wasn't a literal file
# in frontend/dist (i.e. every client-side route like /dashboard).
# Disabling it and doing file-serving ourselves in serve_spa() below
# removes the conflict.
app = Flask(__name__, static_folder=None)
logging.basicConfig(level=logging.INFO)
app.secret_key = config.FLASK_SECRET


class DBLogHandler(logging.Handler):
    """Writes every WARNING+ log record app-wide to the error_log table
    (db.py) so discord_bot.py -- a separate process with no direct
    access to this process's logs or in-memory state -- has something
    to read when someone asks it about recent bugs/errors. See
    discord_bot.py's module docstring for the full read-only boundary.

    A DB write failure here (pool not initialized yet at very early
    startup, a transient connection error, etc.) must never crash the
    app or recurse back into logging -- caught and dropped silently,
    the exact same best-effort shape as every other DB write in this
    codebase (load_persisted_state, _process_trade_signal's
    db.save_trade call, ...).
    """

    def emit(self, record):
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + "".join(traceback.format_exception(*record.exc_info))
            db.save_error_log(level=record.levelname, source=record.name, message=message[:4000])
        except Exception:
            pass


logging.getLogger().addHandler(DBLogHandler(level=logging.WARNING))

WEBHOOK_SECRET = config.WEBHOOK_SECRET
DASHBOARD_PASSWORD = config.DASHBOARD_PASSWORD

# --- Broker + risk manager setup ---------------------------------------
alpaca_creds = config.get_broker_credentials("alpaca")
alpaca_broker = AlpacaBroker(
    api_key=alpaca_creds["api_key"],
    secret_key=alpaca_creds["api_secret"],
    base_url=alpaca_creds["base_url"],
)

oanda_creds = config.get_broker_credentials("oanda")
oanda_broker = OandaBroker(
    api_key=oanda_creds["api_key"],
    account_id=oanda_creds["account_id"],
    base_url=oanda_creds["base_url"],
)

BROKERS = {"stock": alpaca_broker, "forex": oanda_broker, "crypto": alpaca_broker}

# state.risk_caps starts as a deep copy of config.py's hardcoded defaults
# (never the SAME object as config.RISK_CONFIG -- config.py should stay
# untouched compile-time defaults, not something runtime settings changes
# mutate underneath it), then load_persisted_state() below merges in any
# saved overrides. RiskManager holds this exact dict object (not a copy
# of it), so a Settings change that mutates state.risk_caps in place is
# immediately what the risk manager enforces too -- one dict, not two
# numbers that can silently drift apart. See risk/risk_manager.py and
# the Settings API route for the other half of this.
state.risk_caps = copy.deepcopy(config.get_risk_config())
risk_manager = RiskManager(state.risk_caps)

# Hermes stays disabled (its routes return 503) if ANTHROPIC_API_KEY is
# not set -- see hermes.py's init_hermes().
init_hermes(alpaca_broker, oanda_broker, risk_manager)
app.register_blueprint(hermes_bp)

# --- Database setup -----------------------------------------------------
# Fixes the long-standing "everything resets when Railway restarts" issue —
# settings, trade history, and equity curve now persist in Postgres and get
# reloaded into the in-memory state.py cache on every startup.
db.init_pool()
db.init_schema()


def load_persisted_state():
    """Pull settings/trades/equity history from Postgres into the
    in-memory state.py cache. Called once at startup. If the DB is
    unreachable at this exact moment, we log it and boot with state.py's
    hardcoded defaults rather than crashing — a DB hiccup on startup
    shouldn't prevent the bot from running at all."""
    try:
        saved_risk = db.get_setting("risk_percent")
        if saved_risk:
            state.risk_percent.update(saved_risk)

        saved_max = db.get_setting("max_trades_per_day")
        if saved_max:
            state.max_trades_per_day.update(saved_max)

        # Merge per-asset-class, not replace wholesale -- state.risk_caps
        # already has every key config.py currently defines (deep-copied
        # before this runs); a persisted blob saved before a new key like
        # safety_stop_loss_pct existed shouldn't wipe it back out to
        # missing when merged, and RiskManager holds this exact dict
        # object, so mutating it in place (not reassigning state.risk_caps)
        # is what keeps risk_manager.config in sync.
        saved_risk_caps = db.get_setting("risk_caps")
        if saved_risk_caps:
            for ac, overrides in saved_risk_caps.items():
                if ac in state.risk_caps:
                    state.risk_caps[ac].update(overrides)

        saved_enabled = db.get_setting("bot_enabled")
        if saved_enabled is not None:
            state.bot_enabled = saved_enabled

        saved_watchlist = db.get_setting("watched_symbols")
        if saved_watchlist:
            state.watched_symbols.update(saved_watchlist)

        recent_trades = db.get_recent_trades(limit=200)
        state.trade_log = [
            {
                "time": t["executed_at"].isoformat(),  # full datetime -- needed to group by day (calendar heatmap), not just time-of-day
                "action": t["action"],
                "symbol": t["symbol"],
                "asset_class": t["asset_class"],
                "qty": float(t["qty"]),
                "price": str(t["price"]),
                "pnl": float(t["pnl"]) if t["pnl"] is not None else None,
                "regime": t.get("regime"),
                "source": t.get("source"),  # None for trades logged before this column existed -- treated as 'webhook' in the UI
                "explanation": t.get("explanation"),  # None for trades logged before Phase 1 existed
                "strategy_id": t.get("strategy_id"),
            }
            for t in reversed(recent_trades)  # DB gives newest-first, state.py expects oldest-first
        ]

        eq_rows = db.get_equity_history(limit=100)
        state.equity_history = {
            "times": [e["recorded_at"].isoformat() for e in eq_rows],  # full datetime, not just time-of-day -- needed for a real multi-day equity curve
            "values": [float(e["equity"]) for e in eq_rows],
        }

        logging.info(
            "Loaded persisted state: {} trades, {} equity points, settings restored".format(
                len(state.trade_log), len(state.equity_history["times"])
            )
        )
    except Exception as e:
        logging.error("Could not load persisted state from DB, booting with defaults: {}".format(e))


load_persisted_state()

# --- Market regime classifier scheduler ----------------------------------
# Matches each asset class's alert timeframe from TradingView: 30m for
# stock/crypto (shortened from 1h in the "more active" tuning pass —
# forex left at 15m since it was already short relative to its 0.35%
# SL band), 15m for forex. Runs independently of trading itself — a
# failure here should never affect order placement, only logging quality.
_REGIME_TIMEFRAMES = {"stock": "30m", "forex": "15m", "crypto": "30m"}


def run_regime_checks():
    for asset_class, symbols in state.watched_symbols.items():
        broker = BROKERS.get(asset_class)
        if broker is None:
            continue
        timeframe = _REGIME_TIMEFRAMES.get(asset_class, "1h")
        for symbol in symbols:
            try:
                result = regime.run_regime_check(
                    broker, symbol, asset_class, config.get_regime_config(),
                    timeframe=timeframe, db_module=db,
                )
                logging.info("Regime check {}: {}".format(symbol, result))
            except Exception as e:
                logging.warning("Regime check failed for {}: {}".format(symbol, e))
                if isinstance(e, BrokerConnectionError):
                    alerts.record_broker_error(detail=traceback.format_exc())


def _call_process_trade_signal_in_context(action, symbol, is_manual, source='webhook', force_close_qty=None):
    """Runs _process_trade_signal() inside app.app_context() -- needed
    because it calls jsonify() internally, and every caller of this
    helper runs on webhook_queue's background worker thread, outside any
    Flask request context (same reasoning as _process_queued_webhook_signal,
    which does the same thing specifically for /webhook's own durable-
    queue bookkeeping -- this is the plain version for the other three
    callers, which have no such bookkeeping of their own).

    ALWAYS called via webhook_queue.enqueue_and_wait(), never directly --
    run_position_safety_checks(), /api/manual_close, and
    /api/strategies/assign all route through the exact same per-symbol
    queue /webhook does, specifically so none of them can race a
    same-symbol signal from any of the others. See webhook_queue.py's
    module docstring for the full picture."""
    with app.app_context():
        return _process_trade_signal(action, symbol, is_manual, source, force_close_qty)


def run_position_safety_checks():
    """Independent backstop against a stuck losing position: if any
    open position's unrealized loss breaches its asset class's
    safety_stop_loss_pct (config.py), force-close it via the SAME
    order-placement/logging/persistence path a normal trade uses
    (_process_trade_signal with force_close_qty set), regardless of
    whether TradingView ever sends a matching exit webhook.

    This is deliberately NOT the strategy's own stop-loss -- it's a
    last-resort safety net for when something's actually gone wrong
    (a missed/failed exit alert, TradingView down, a webhook silently
    rejected, etc.), not a substitute for the strategy's own exit
    logic. See config.py's safety_stop_loss_pct docstring for why the
    threshold is looser than the strategy's own intended stop.

    The actual force-close is routed through webhook_queue.enqueue_and_wait()
    (same per-symbol queue /webhook uses) rather than calling
    _process_trade_signal directly -- this used to be the one path that
    could race a same-symbol webhook signal arriving at nearly the same
    moment (both calling broker.place_order concurrently); now it
    strictly queues behind (or ahead of, whichever arrived first)
    anything else in flight for that symbol. This DOES mean a force-
    close is no longer necessarily instantaneous if something else for
    the same symbol is already being processed -- acceptable given
    actual traffic (a handful of signals/day; queue depth is normally
    zero) and explicitly signed off on given it closes a real ordering
    gap.

    Runs independently of trading itself, same as run_regime_checks --
    a failure checking one position should never prevent checking (or
    force-closing) the others.
    """
    risk_config = config.get_risk_config()
    for p in get_all_positions():
        rules = risk_config.get(p["asset_class"], {})
        threshold_pct = rules.get("safety_stop_loss_pct")
        if not threshold_pct:
            continue

        cost_basis = p["qty"] * p["avg_entry"]
        if cost_basis <= 0 or p["unrealized_pl"] >= 0:
            continue

        loss_pct = abs(p["unrealized_pl"]) / cost_basis
        if loss_pct < threshold_pct:
            continue

        close_action = "sell" if p["direction"] == "long" else "buy"
        logging.error(
            "SAFETY NET: {} {} unrealized loss {:.2f}% >= {:.2f}% threshold -- force-closing ({} {})".format(
                p["asset_class"], p["symbol"], loss_pct * 100, threshold_pct * 100, close_action, p["qty"],
            )
        )
        try:
            result = webhook_queue.enqueue_and_wait(
                p["symbol"],
                lambda p=p, close_action=close_action: _call_process_trade_signal_in_context(
                    close_action, p["symbol"], is_manual=False,
                    source="safety_stop_loss", force_close_qty=p["qty"],
                ),
            )
            if isinstance(result, tuple) and result[1] != 200:
                logging.error("Safety-net force-close for {} did not succeed (status {})".format(p["symbol"], result[1]))
        except Exception as e:
            logging.error("Safety-net force-close FAILED for {}: {}".format(p["symbol"], e))
            if isinstance(e, BrokerConnectionError):
                alerts.record_broker_error(detail=traceback.format_exc())


def _persist_health_snapshot():
    """Best-effort snapshot of risk_manager's halt state + auth-failure
    counts + webhook timing into the bot_settings table (db.py) --
    same key-value store everything else in Settings already uses, just
    a new key. This is how discord_bot.py (a SEPARATE process, started
    from the Procfile's "worker" entry) reads this data: it has no
    direct access to THIS process's in-memory risk_manager/state.py
    objects, so Postgres is the handoff point. Refreshed every
    run_alert_checks cycle (5 min) -- a reader should treat this as
    accurate as of 'updated_at', not real-time.

    'last_webhook_at' mirrors state.last_webhook_at exactly -- a dict
    keyed by SYMBOL (not asset class), each entry set for EVERY inbound
    /webhook call carrying that symbol regardless of whether the call
    passes the secret check (see webhook() below) -- not just
    authenticated ones. discord_bot.py should describe it that way
    rather than as "last successful hit".
    """
    try:
        db.save_setting("health_snapshot", {
            "account_halted": risk_manager.account_halted,
            "trading_halted": dict(risk_manager.trading_halted),
            "failed_login_attempts": len(state.failed_login_attempts),
            "failed_webhook_attempts": len(state.failed_webhook_attempts),
            "last_webhook_at": state.last_webhook_at,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
    except Exception as e:
        logging.warning("Could not persist health snapshot to DB: {}".format(e))


def run_alert_checks():
    """Posts to Discord (via alerts.py) for any of three conditions that
    just tripped: the account or an asset class getting halted by the
    risk manager, /webhook going quiet too long during market hours, and
    broker errors piling up. See alerts.py's module docstring for the
    edge-triggered/latched alerting model and config.DISCORD_ALERT_WEBHOOK_URL
    for how this is fully disabled when unconfigured.

    Also persists a health snapshot for discord_bot.py on the same
    5-minute cycle -- see _persist_health_snapshot().

    Runs independently of trading itself, same as run_regime_checks and
    run_position_safety_checks -- a failure in one check (or in Discord
    itself) should never affect order placement or block the others.
    """
    try:
        alerts.check_and_alert_bot_halted(risk_manager)
    except Exception as e:
        logging.warning("Alert check (bot halted) failed: {}".format(e))
    try:
        alerts.check_and_alert_webhook_silence()
    except Exception as e:
        logging.warning("Alert check (webhook silence) failed: {}".format(e))
    try:
        alerts.check_and_alert_broker_errors()
    except Exception as e:
        logging.warning("Alert check (broker errors) failed: {}".format(e))
    _persist_health_snapshot()


scheduler = BackgroundScheduler(daemon=True)
# next_run_time defaults to "now" for an interval trigger when omitted,
# so this fires an immediate first check on boot, then every 15 minutes
# after. A previous version passed next_run_time=None here, which in
# APScheduler means "add this job PAUSED" -- nothing ever resumed it,
# so this job silently never ran even once, market_regime stayed
# permanently empty, and every regime lookup (dashboard widget, trade
# tagging) fell back to "unknown" forever. That's the actual root
# cause of regime always showing unknown, not a classification bug.
scheduler.add_job(run_regime_checks, "interval", minutes=15, next_run_time=datetime.datetime.now())
# Checked far more often than regime (5 min vs 15) since this is the
# thing standing between a losing position and an unbounded loss if
# TradingView ever stops sending exit signals -- see the function's
# docstring. Also fires immediately on boot for the same reason
# next_run_time=None caused the regime bug above: never leave a safety
# job in APScheduler's paused-by-default state.
scheduler.add_job(run_position_safety_checks, "interval", minutes=5, next_run_time=datetime.datetime.now())
# Same cadence as the safety-net check above -- 5 minutes is frequent
# enough to catch a fresh halt or an error spike quickly without being
# noisy, and matches the 15-minute broker-error window/2-hour webhook-
# silence threshold in alerts.py closely enough that a condition won't
# sit undetected for long. Immediate first run for the same
# don't-leave-a-job-paused reason as the two jobs above.
scheduler.add_job(run_alert_checks, "interval", minutes=5, next_run_time=datetime.datetime.now())
scheduler.start()
# --- end market regime classifier scheduler ------------------------------


# Escalating-visibility-only failed-auth tracking -- see state.py's
# failed_login_attempts/failed_webhook_attempts docstring for why this
# deliberately does NOT auto-block anything. _LOCKOUT_WINDOW_SECONDS is
# just the window failures are counted/reported over, not a lockout
# duration (nothing here locks anyone out).
_FAILED_ATTEMPT_WINDOW_SECONDS = 900  # 15 minutes


def _record_failed_attempt(attempts_list):
    """Appends now(), prunes anything outside the window, and returns
    the count still in-window -- used to log an increasingly loud
    warning the more failures pile up recently."""
    now = time.time()
    attempts_list.append(now)
    cutoff = now - _FAILED_ATTEMPT_WINDOW_SECONDS
    while attempts_list and attempts_list[0] < cutoff:
        attempts_list.pop(0)
    return len(attempts_list)


def _get_client_ip():
    """Best-effort real client IP behind Railway's reverse proxy.
    X-Forwarded-For's FIRST entry is the original client when set by a
    well-behaved proxy; falls back to Flask's own request.remote_addr
    (which, behind a proxy, is usually the proxy's own IP, not the real
    client -- exactly why WEBHOOK_IP_MODE defaults to 'off' and has a
    'log' shadow mode: verify this actually resolves to TradingView's
    real IPs in Railway's setup before ever enforcing on it)."""
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr


def asset_class_for_symbol(symbol):
    """Crypto pairs use Alpaca's slash format, e.g. BTC/USD.
    Forex pairs use OANDA's underscore format, e.g. EUR_USD.
    Anything else (AAPL, TSLA, ...) is treated as a stock/Alpaca symbol.

    ONLY safe for symbols in this app's own formats (webhook payloads,
    watched_symbols, ...). Symbols coming back FROM Alpaca's API strip
    the slash ('BTCUSD') and misclassify as stock here -- normalize with
    _normalize_alpaca_crypto_symbol first, or better, trust the API
    response's own asset_class field (see get_all_positions)."""
    if "/" in symbol:
        return "crypto"
    if "_" in symbol:
        return "forex"
    return "stock"


# Quote currencies Alpaca supports for crypto pairs, longest first so
# e.g. 'DOGEUSDT' splits as DOGE/USDT rather than mis-splitting on the
# shorter 'USD' suffix match.
_ALPACA_CRYPTO_QUOTES = ("USDT", "USDC", "USD", "BTC")


def _normalize_alpaca_crypto_symbol(symbol):
    """Alpaca API responses (positions, orders) return crypto symbols
    WITHOUT the pair separator ('BTCUSD'), while everything in this app
    -- and Alpaca's own order/market-data endpoints -- speaks the slash
    format ('BTC/USD'). Reinserts the slash before the quote currency;
    already-slashed or unrecognized symbols pass through unchanged."""
    if "/" in symbol:
        return symbol
    for quote in _ALPACA_CRYPTO_QUOTES:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)] + "/" + quote
    return symbol


def _get_held_qty(broker, symbol):
    """Current held quantity for `symbol` on an Alpaca-backed broker
    (stock/crypto), or 0.0 if the bot doesn't currently hold a position
    in it. Used to gate sells to what's actually held -- see
    _process_trade_signal.

    `symbol` arrives in this app's slash format ('BTC/USD') while
    Alpaca's positions come back without the separator ('BTCUSD') --
    normalize before comparing, or a crypto sell never matches its own
    real position and gets wrongly rejected with 'no position to sell'.

    Retries get_positions() once if the symbol isn't found on the first
    read -- a transient gap in Alpaca's own position data can otherwise
    look identical to genuinely holding nothing (seen live: a real
    position came back empty on the first read, wrongly rejecting a
    sell as 'no position to sell')."""
    def _find(positions):
        for p in positions:
            held_symbol = p.symbol
            if getattr(p, 'asset_class', None) == 'crypto':
                held_symbol = _normalize_alpaca_crypto_symbol(held_symbol)
            if held_symbol == symbol:
                return float(p.qty)
        return None

    try:
        qty = _find(broker.get_positions())
        if qty is None:
            qty = _find(broker.get_positions())
        if qty is not None:
            return qty
    except BrokerConnectionError as e:
        logging.warning("Could not check held position for {}: {}".format(symbol, e))
    return 0.0


def _compute_current_signal(broker, symbol, asset_class, params=None):
    """Fetches recent OHLCV and returns backtest.strategy.compute_signals()'s
    LATEST per-bar signal dict for `symbol` right now, or None if there
    isn't enough history yet or the broker call fails. Shared by
    _sanity_check_signal (which also compares it against the live
    webhook's claimed action) and trade_explanations.py's entry-
    explanation generator (which just needs the raw indicator values,
    not a comparison) -- both read off the exact same live computation
    instead of two independent broker fetches.

    `params` defaults to backtest.strategy.DEFAULT_PARAMS if not given
    (this function's original behavior, before per-symbol strategy
    assignment existed) -- callers should pass the symbol's assigned
    strategy's params (db.get_symbol_strategy_assignment) when available,
    so this reflects what's actually configured for the symbol rather
    than the generic default.

    Never raises -- any failure here (broker error, not enough bars,
    etc.) is logged and swallowed, matching every other best-effort side
    computation in this codebase (regime tagging, etc.) -- this must
    never be the reason a real trade signal fails to execute.
    """
    try:
        timeframe = _REGIME_TIMEFRAMES.get(asset_class, "1h")
        bars = broker.get_ohlcv(symbol, timeframe=timeframe, limit=100)
        if len(bars) < 40:  # not enough history to warm up EMA-slow(21)/RSI(14)/lookback(7) reliably
            return None
        signals = compute_signals(bars, params)
        return signals[-1]
    except Exception as e:
        logging.warning("Could not compute current signal for {}: {}".format(symbol, e))
        return None


def _sanity_check_signal(broker, symbol, asset_class, action, params=None):
    """Defense in depth: independently recompute the Higher High
    Breakout strategy's signal for `symbol` RIGHT NOW using the same
    Python port the backtester uses (backtest/strategy.py), and log a
    warning if it disagrees with the action an incoming webhook just
    claimed TradingView's own Pine Script fired.

    This does NOT block the trade -- it's purely observational. Two
    reasons it's log-only rather than a hard gate: (1) subtle timing/
    bar-close differences between this call and whatever bar Pine
    Script actually evaluated on can cause a disagreement even when
    both are "correct" for the instant they each looked at, and (2) it
    assumes the live TradingView alert is running on the SAME timeframe
    as _REGIME_TIMEFRAMES assumes for this asset class -- if it isn't,
    every comparison here is meaningless noise, not a real problem.
    Treat a logged disagreement as "worth a look", not "the bot is
    broken".

    Returns the computed signal dict (or None -- see
    _compute_current_signal) so callers can reuse it for other purposes
    (currently: entry trade explanations) without a second broker fetch.
    """
    latest = _compute_current_signal(broker, symbol, asset_class, params)
    if latest is None:
        return None

    timeframe = _REGIME_TIMEFRAMES.get(asset_class, "1h")
    if action == "buy" and not latest["buy_condition"]:
        logging.warning(
            "SANITY CHECK: webhook says BUY {} but the Python strategy port doesn't currently see a buy "
            "condition on the {} timeframe (ema_fast={}, ema_slow={}, rsi={}, breakout_price={}). "
            "Not blocking the trade -- just flagging a possible signal/timeframe mismatch.".format(
                symbol, timeframe, latest["ema_fast"], latest["ema_slow"], latest["rsi"], latest["breakout_price"],
            )
        )
    elif action == "sell" and not latest["sell_signal"]:
        logging.warning(
            "SANITY CHECK: webhook says SELL {} but the Python strategy port doesn't currently see its "
            "momentum-exit condition on the {} timeframe (ema_fast={}, close vs ema_fast trend flip check). "
            "Not blocking the trade -- just flagging a possible signal/timeframe mismatch.".format(
                symbol, timeframe, latest["ema_fast"]
            )
        )
    return latest


def get_all_positions():
    """Open positions across both brokers with RAW numeric fields (not
    display-formatted) -- shared by /api/dashboard (which formats them
    for display) and run_position_safety_checks() (which needs to do
    math on them). Each dict's 'qty' is always a positive magnitude;
    direction ('long'/'short') is reported separately in 'direction'
    rather than as a sign on qty, so callers never have to remember
    which convention applies to which broker. Stock/crypto (Alpaca)
    are always 'long' here since this bot never shorts them; forex
    (OANDA) can be either.
    """
    positions = []
    try:
        for p in alpaca_broker.get_positions():
            # Alpaca's own asset_class field ('us_equity'/'crypto') is
            # authoritative -- position symbols come back WITHOUT the
            # pair separator ('BTCUSD'), which asset_class_for_symbol's
            # slash heuristic misreads as a stock ticker. That exact
            # misclassification sent the safety-net monitor's force-close
            # down the stock order path, where Alpaca rejected it ('no
            # trade found for BTCUSD') every 5 minutes forever without
            # the position ever closing. The symbol is normalized back
            # to slash form too, so everything downstream (force-close
            # orders, price lookups, the dashboard) gets the format the
            # rest of the app -- and Alpaca's own order endpoint -- speaks.
            symbol = p.symbol
            raw_class = getattr(p, 'asset_class', None)
            if raw_class == 'crypto':
                ac = 'crypto'
                symbol = _normalize_alpaca_crypto_symbol(symbol)
            elif raw_class is not None:
                ac = 'stock'
            else:
                ac = asset_class_for_symbol(symbol)  # very old API shape with no asset_class -- fall back to the heuristic
            qty = float(p.qty)
            # 'qty_available' (Alpaca's own field) can be LESS than 'qty'
            # when some of the position is already tied up in another
            # open, unfilled order -- e.g. a "day" market order submitted
            # while the market's closed sits queued (not filled) until
            # the next session, and Alpaca won't let a second order claim
            # shares the first one already reserved. Falls back to 'qty'
            # (fully available) if this API response shape ever omits the
            # field, rather than falsely reporting a lock and blocking
            # every close.
            qty_available = float(getattr(p, 'qty_available', qty))
            positions.append({
                'symbol': symbol, 'asset_class': ac, 'direction': 'long',
                'qty': qty, 'qty_available': qty_available, 'avg_entry': float(p.avg_entry_price),
                'current_price': float(p.current_price),
                'unrealized_pl': float(p.unrealized_pl),
            })
    except BrokerConnectionError:
        alerts.record_broker_error(detail=traceback.format_exc())

    try:
        for p in oanda_broker.get_positions():
            long_units = float(p.get('long', {}).get('units', 0))
            short_units = float(p.get('short', {}).get('units', 0))
            direction = 'long' if long_units != 0 else 'short' if short_units != 0 else None
            if direction is None:
                continue
            qty = long_units if direction == 'long' else abs(short_units)
            avg_price = p.get('long', {}).get('averagePrice') if direction == 'long' else p.get('short', {}).get('averagePrice')
            unrealized = float(p.get('long', {}).get('unrealizedPL', 0)) + float(p.get('short', {}).get('unrealizedPL', 0))
            positions.append({
                'symbol': p['instrument'], 'asset_class': 'forex', 'direction': direction,
                # OANDA orders fill effectively instantly (no Alpaca-style
                # queued "day" order that can partially lock a position),
                # so there's no equivalent reserved/unavailable concept
                # here -- the full qty is always available to close.
                'qty': qty, 'qty_available': qty,
                'avg_entry': float(avg_price or 0),
                'current_price': None,
                'unrealized_pl': unrealized,
            })
    except BrokerConnectionError:
        alerts.record_broker_error(detail=traceback.format_exc())

    return positions


def get_combined_equity():
    """Best-effort combined equity across DISTINCT brokers. Stock and
    crypto share the same Alpaca account/equity, so we dedupe by broker
    identity here — otherwise Alpaca's balance would get counted twice
    and make the account-wide circuit breaker math wrong."""
    total = 0.0
    got_any = False
    seen_brokers = []
    for broker in BROKERS.values():
        if any(broker is seen for seen in seen_brokers):
            continue
        seen_brokers.append(broker)
        try:
            total += broker.get_account_info()["equity"]
            got_any = True
        except BrokerConnectionError as e:
            logging.warning("Could not fetch equity from a broker: {}".format(e))
            alerts.record_broker_error(detail=traceback.format_exc())
    if not got_any:
        raise BrokerConnectionError("Could not reach either broker to compute combined equity")
    return total


def check_daily_rollover():
    """Reset daily counters (trades_today, risk_manager's daily P&L)
    the first time a request comes in on a new day."""
    today = datetime.date.today().isoformat()
    if state.current_day != today:
        state.current_day = today
        state.trades_today = {"stock": 0, "forex": 0, "crypto": 0}
        try:
            risk_manager.reset_daily(get_combined_equity())
        except BrokerConnectionError:
            risk_manager.reset_daily(None)


# --- Routes -----------------------------------------------------------

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json or {}
    # Constant-time comparison -- a plain == leaks timing information
    # proportional to how many leading characters match, which is a
    # real (if slow/impractical) attack against a string compared over
    # the network repeatedly. hmac.compare_digest closes that off for
    # free.
    if hmac.compare_digest(str(data.get('password', '')), DASHBOARD_PASSWORD):
        state.failed_login_attempts.clear()
        session['auth'] = True
        return jsonify({'status': 'ok'})
    count = _record_failed_attempt(state.failed_login_attempts)
    log_level = logging.ERROR if count >= 5 else logging.WARNING
    logging.log(log_level, 'Failed dashboard login attempt ({} in the last {} min)'.format(count, _FAILED_ATTEMPT_WINDOW_SECONDS // 60))
    return jsonify({'error': 'invalid password'}), 401


@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'status': 'ok'})


@app.route('/api/session')
def api_session():
    return jsonify({'authenticated': bool(session.get('auth'))})


@app.route('/api/dashboard')
def api_dashboard():
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401

    check_daily_rollover()

    try:
        stock_acct = alpaca_broker.get_account_info()
    except BrokerConnectionError as e:
        logging.error("Alpaca account fetch failed: {}".format(e))
        stock_acct = {"equity": 0.0, "buying_power": 0.0, "last_equity": 0.0}

    try:
        forex_acct = oanda_broker.get_account_info()
    except BrokerConnectionError as e:
        logging.error("OANDA account fetch failed: {}".format(e))
        forex_acct = {"equity": 0.0, "buying_power": 0.0, "last_equity": 0.0}

    combined_equity = stock_acct["equity"] + forex_acct["equity"]

    now = datetime.datetime.now().isoformat(timespec='minutes')
    if not state.equity_history['times'] or state.equity_history['times'][-1] != now:
        state.equity_history['times'].append(now)
        state.equity_history['values'].append(round(combined_equity, 2))
        if len(state.equity_history['times']) > 100:
            state.equity_history['times'].pop(0)
            state.equity_history['values'].pop(0)
        try:
            db.save_equity_point(combined_equity)
        except Exception as e:
            logging.warning('Could not persist equity point to DB: {}'.format(e))

    raw_positions = get_all_positions()
    positions = []
    for p in raw_positions:
        price_fmt = '{:.5f}' if p['asset_class'] == 'forex' else '{:.4f}' if p['asset_class'] == 'crypto' else '{:.2f}'
        signed_qty = p['qty'] if p['direction'] == 'long' else -p['qty']
        positions.append({
            'symbol': p['symbol'], 'qty': signed_qty, 'asset_class': p['asset_class'],
            'avg_entry': price_fmt.format(p['avg_entry']),
            'current_price': price_fmt.format(p['current_price']) if p['current_price'] is not None else '—',
            'unrealized_pl': round(p['unrealized_pl'], 2),
        })

    completed = [t for t in state.trade_log if t.get('pnl') is not None]
    wins = [t for t in completed if t['pnl'] > 0]
    losses = [t for t in completed if t['pnl'] < 0]
    win_rate = round(len(wins) / len(completed) * 100) if completed else 0
    avg_gain = round(sum(t['pnl'] for t in wins) / len(wins), 2) if wins else 0
    avg_loss = round(abs(sum(t['pnl'] for t in losses) / len(losses)), 2) if losses else 0
    best_trade = round(max([t['pnl'] for t in wins] or [0]), 2)
    worst_trade = round(abs(min([t['pnl'] for t in losses] or [0])), 2)

    regimes = []
    for asset_class, symbols in state.watched_symbols.items():
        for sym in symbols:
            try:
                r = db.get_latest_regime(sym)
            except Exception:
                r = None
            regimes.append({
                'symbol': sym,
                'asset_class': asset_class,
                'regime': r['regime'] if r else 'unknown',
            })

    return jsonify({
        'trading_mode': config.TRADING_MODE,
        'combined_equity': round(combined_equity, 2),
        'stock_account': stock_acct,
        'forex_account': forex_acct,
        'risk_state': {
            'stock_halted': risk_manager.trading_halted['stock'],
            'forex_halted': risk_manager.trading_halted['forex'],
            'crypto_halted': risk_manager.trading_halted['crypto'],
            'account_halted': risk_manager.account_halted,
            'starting_equity_today': round(risk_manager.starting_equity_today, 2) if risk_manager.starting_equity_today else None,
            'daily_pnl': {ac: round(risk_manager.daily_pnl[ac], 2) for ac in risk_manager.asset_classes},
        },
        'positions': positions,
        'trades': state.trade_log,
        'equity_history': {
            'times': state.equity_history['times'],
            'values': state.equity_history['values'],
        },
        'watched_symbols': state.watched_symbols,
        'bot_enabled': state.bot_enabled,
        'risk_percent': state.risk_percent,
        'max_trades_per_day': state.max_trades_per_day,
        'trades_today': state.trades_today,
        'trade_stats': {
            'win_rate': win_rate, 'avg_gain': avg_gain, 'avg_loss': avg_loss,
            'best_trade': best_trade, 'worst_trade': worst_trade,
        },
        'regimes': regimes,
        'risk_caps': state.risk_caps,  # live/editable, not the static config.py defaults -- see Settings
    })


@app.route('/api/toggle_bot', methods=['POST'])
def toggle_bot():
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401
    state.bot_enabled = not state.bot_enabled
    try:
        db.save_setting('bot_enabled', state.bot_enabled)
    except Exception as e:
        logging.warning('Could not persist bot_enabled to DB: {}'.format(e))
    return jsonify({'enabled': state.bot_enabled})


# Fields in state.risk_caps a Settings request is allowed to change.
# The three *_pct fields are stored internally as FRACTIONS (0.02 =
# 2%, matching config.py's convention and everything that reads
# state.risk_caps), but the API/UI convention everywhere else in this
# app (risk_percent, the Settings sliders) is a plain percentage
# number (2, not 0.02) -- so those three divide by 100 on the way in.
# max_open_positions/max_leverage aren't percentages and pass through
# as-is. max_leverage is forex-only (crypto/stock configs simply omit
# the key -- see config.py) but included here uniformly; setting it
# for stock/crypto is harmless since nothing reads it for those asset
# classes.
_RISK_CAP_FIELDS = {
    'max_position_size_pct': lambda v: float(v) / 100.0,
    'max_daily_loss_pct': lambda v: float(v) / 100.0,
    'safety_stop_loss_pct': lambda v: float(v) / 100.0,
    'max_open_positions': lambda v: int(v),
    'max_leverage': lambda v: float(v),
}


@app.route('/api/settings', methods=['POST'])
def settings():
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401
    data = request.json or {}
    asset_class = data.get('asset_class')
    if asset_class not in ('stock', 'forex', 'crypto'):
        return jsonify({'error': 'asset_class must be stock, forex, or crypto'}), 400
    if 'risk_percent' in data:
        state.risk_percent[asset_class] = int(data['risk_percent'])
        try:
            db.save_setting('risk_percent', state.risk_percent)
        except Exception as e:
            logging.warning('Could not persist risk_percent to DB: {}'.format(e))
    if 'max_trades_per_day' in data:
        state.max_trades_per_day[asset_class] = int(data['max_trades_per_day'])
        try:
            db.save_setting('max_trades_per_day', state.max_trades_per_day)
        except Exception as e:
            logging.warning('Could not persist max_trades_per_day to DB: {}'.format(e))

    risk_caps_changed = False
    for field, coerce in _RISK_CAP_FIELDS.items():
        if field in data:
            try:
                state.risk_caps[asset_class][field] = coerce(data[field])
                risk_caps_changed = True
            except (TypeError, ValueError):
                return jsonify({'error': 'invalid value for {}'.format(field)}), 400
    if risk_caps_changed:
        try:
            db.save_setting('risk_caps', state.risk_caps)
        except Exception as e:
            logging.warning('Could not persist risk_caps to DB: {}'.format(e))

    return jsonify({'status': 'updated'})


@app.route('/api/watchlist', methods=['POST'])
def add_watchlist():
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401
    data = request.json or {}
    sym = data.get('symbol', '').upper()
    asset_class = data.get('asset_class') or asset_class_for_symbol(sym)
    if asset_class not in ('stock', 'forex', 'crypto'):
        return jsonify({'error': 'invalid asset_class'}), 400
    if sym and sym not in state.watched_symbols[asset_class]:
        state.watched_symbols[asset_class].append(sym)
        try:
            db.save_setting('watched_symbols', state.watched_symbols)
        except Exception as e:
            logging.warning('Could not persist watched_symbols to DB: {}'.format(e))
        return jsonify({'status': 'added'})
    return jsonify({'status': 'exists'})


@app.errorhandler(400)
@app.errorhandler(415)
def _handle_webhook_parse_failure(e):
    """Flask raises these (via request.json) BEFORE webhook() below ever
    runs, for a wrong Content-Type (415) or a body that isn't valid JSON
    at all (400 -- e.g. smart/curly quotes from copy-pasting a
    TradingView alert message, a trailing comma, an unclosed brace).
    Without this handler, that failure is completely invisible to
    us -- webhook()'s own logging never executes, so nothing reaches
    error_log and the only trace is TradingView's own alert-delivery
    log. Only special-cased for /webhook: every other route's explicit
    `return jsonify(...), 400` is a normal return value, not a raised
    exception, so this handler never sees those and can't affect them.
    """
    if request.path == '/webhook':
        raw_body = request.get_data(as_text=True) or ''
        if WEBHOOK_SECRET:
            raw_body = raw_body.replace(WEBHOOK_SECRET, 'REDACTED')
        logging.warning(
            'Webhook request rejected before reaching /webhook\'s own validation '
            '(Content-Type={!r}, HTTP {} {}) -- check for malformed JSON in the '
            'TradingView alert message (smart quotes, trailing comma, missing '
            'brace, etc). Raw body (secret redacted): {}'.format(
                request.content_type, e.code, e.name, raw_body[:500]
            )
        )
    return e.get_response()


@app.route('/webhook', methods=['POST'])
def webhook():
    """External-facing route for TradingView alerts — requires the shared
    WEBHOOK_SECRET, never exposed to the browser.

    Everything below through the validation checks (secret, IP allowlist,
    missing/placeholder fields, strategy_id) runs synchronously and stays
    FAST -- but "fast" here means "no broker calls", not "no DB calls":
    the strategy-switch safety check and the durable-queue INSERT
    (db.enqueue_webhook_signal) are both single, indexed Postgres
    operations, the same cost class as save_trade elsewhere in this file
    -- nothing like the multi-call broker round-trip chain that actually
    caused the timeout problem this route's async design exists to fix
    (see below). A malformed, unauthorized, or stale-strategy call still
    gets an immediate, definitive answer. Once a signal passes all of
    that, actually EXECUTING it (sanity check, broker account/price
    lookups, order placement -- see _process_trade_signal) is hands-off
    to webhook_queue.enqueue() instead of being awaited here: two real
    TradingView delivery-timeout incidents traced back to that broker
    call chain taking long enough (confirmed up to ~2.85s) that
    TradingView gave up waiting on the response -- even though, in both
    cases, the trade had ALREADY executed successfully server-side by
    then. This route now returns 202 the moment a signal is queued,
    meaning "accepted for processing", NOT "trade placed" -- the real
    outcome (placed, rejected, or errored) is still logged exactly as
    before by _process_trade_signal itself (and still lands in error_log
    via DBLogHandler for anything WARNING+), just after this response
    has already gone out. webhook_queue guarantees per-symbol ordering: a
    sell landing shortly after a buy for the same symbol is queued
    behind it and never races it, even though a different symbol's
    signal runs fully in parallel. /api/manual_trade and /api/manual_close
    are UNCHANGED (still synchronous) -- their callers are the dashboard
    itself, actively waiting on the real outcome to render, not an
    external service with its own delivery timeout."""
    data = request.json

    # Log every inbound webhook call regardless of outcome (secret
    # redacted) -- this is the only way to see what TradingView actually
    # sent when a failure never makes it into _process_trade_signal's
    # own rejection logging below (e.g. malformed JSON, missing fields).
    safe_data = dict(data) if data else data
    if safe_data and 'secret' in safe_data:
        safe_data['secret'] = 'REDACTED'
    logging.info('Webhook received: {}'.format(safe_data))

    # Per-symbol silence clock: stamped for EVERY inbound call that
    # carries a usable symbol, before the secret check below -- the
    # webhook-silence alert (alerts.py) is about whether anything is
    # reaching this endpoint for that symbol at all, not just
    # well-authenticated hits. Only the incoming symbol ITSELF gets
    # stamped: a busy symbol (e.g. NVDA firing every 30m) resetting a
    # single shared per-asset-class clock used to mask a DIFFERENT
    # symbol in the same class (e.g. AAPL) going silent at the same
    # time. Also clears that symbol's silence latch, so a later separate
    # silent stretch can alert again instead of staying silenced forever
    # after the first. A call with no symbol at all (malformed JSON,
    # missing fields) has no symbol to attribute and stamps nothing.
    inbound_symbol = (data or {}).get('symbol')
    if inbound_symbol and inbound_symbol not in ('{{TICKER}}', '{{ticker}}'):
        state.last_webhook_at[inbound_symbol] = time.time()
        state.alerted_webhook_silence[inbound_symbol] = False

    if not data:
        logging.warning('Rejected webhook call: no JSON body')
        return jsonify({'error': 'no data'}), 415
    # Constant-time comparison, same reasoning as /api/login. Note this
    # can't be a real HMAC signature (TradingView alerts send a fixed,
    # static message body defined when the alert is created -- there's
    # no way for TradingView to compute a per-request signature at fire
    # time), so a shared secret string is the strongest auth this
    # webhook can realistically have. Deliberately not locking out
    # after repeated failures either -- see state.py's
    # failed_webhook_attempts docstring for why that risks blocking
    # real trade signals.
    if not hmac.compare_digest(str(data.get('secret', '')), WEBHOOK_SECRET):
        count = _record_failed_attempt(state.failed_webhook_attempts)
        log_level = logging.ERROR if count >= 5 else logging.WARNING
        logging.log(log_level, 'Rejected webhook call: bad secret ({} failed attempts in the last {} min)'.format(count, _FAILED_ATTEMPT_WINDOW_SECONDS // 60))
        return jsonify({'error': 'unauthorized'}), 401
    state.failed_webhook_attempts.clear()

    if config.WEBHOOK_IP_MODE in ('log', 'enforce'):
        client_ip = _get_client_ip()
        if client_ip not in config.TRADINGVIEW_WEBHOOK_IPS:
            msg = "Webhook call from {} is not in TradingView's published IP list".format(client_ip)
            if config.WEBHOOK_IP_MODE == 'enforce':
                logging.warning(msg + ' -- rejecting (WEBHOOK_IP_MODE=enforce)')
                return jsonify({'error': 'unauthorized'}), 401
            logging.warning(msg + ' -- NOT rejecting (WEBHOOK_IP_MODE=log, shadow mode only)')

    action = data.get('action')
    symbol = data.get('symbol')
    if not action or not symbol:
        logging.warning('Rejected webhook call: missing fields (action={!r}, symbol={!r})'.format(action, symbol))
        return jsonify({'error': 'missing fields'}), 400
    if symbol in ('{{TICKER}}', '{{ticker}}'):
        logging.warning('Rejected webhook call: unsubstituted symbol placeholder')
        return jsonify({'error': 'invalid symbol'}), 400

    # Strategy-switch safety gate (Phase 4): if THIS alert has been
    # migrated to include strategy_id in its message JSON, it must match
    # whatever's CURRENTLY assigned as active for this symbol -- a
    # mismatch means a stale alert (still running an old Pine variant,
    # or pointed at a symbol that's since been switched to a different
    # strategy via /api/strategies/assign) is firing, and gets rejected
    # instead of silently executing under out-of-date logic. Alerts that
    # DON'T send strategy_id at all (not yet migrated -- every alert live
    # today, until updated) are NOT gated: the field's mere presence is
    # what turns this check on, per-alert, so migrating one alert at a
    # time can never break the others mid-rollout.
    #
    # A DB error verifying this is NOT treated as a mismatch -- same "a
    # DB hiccup must never be the reason a real trade signal gets
    # rejected outright" rule this codebase applies everywhere else
    # (e.g. the durability INSERT right below). Only a SUCCESSFUL lookup
    # that actually disagrees (or finds no assignment at all) rejects.
    incoming_strategy_id = data.get('strategy_id')
    if incoming_strategy_id is not None:
        assignment = None
        verifiable = True
        try:
            assignment = db.get_symbol_strategy_assignment(symbol)
        except Exception as e:
            verifiable = False
            logging.error(
                'Could not verify strategy_id for {} (DB error) -- processing the signal anyway '
                'rather than rejecting a real trade signal over an availability issue: {}'.format(symbol, e)
            )
        if verifiable and assignment is None:
            logging.warning(
                'Rejected webhook call: {} sent strategy_id={!r} but {} has no active strategy '
                'assignment at all -- rejecting rather than guessing which logic should own this '
                'signal.'.format(symbol, incoming_strategy_id, symbol)
            )
            return jsonify({'error': 'no active strategy assignment for {}'.format(symbol)}), 409
        if verifiable and assignment is not None and assignment['id'] != incoming_strategy_id:
            logging.warning(
                'Rejected webhook call: {} sent strategy_id={!r} but the currently active strategy '
                'for {} is #{} ({}) -- likely a stale alert from before a strategy switch.'.format(
                    symbol, incoming_strategy_id, symbol, assignment['id'], assignment['name'],
                )
            )
            return jsonify({
                'error': 'stale strategy_id for {} (active strategy is #{})'.format(symbol, assignment['id'])
            }), 409

    manual_flag = data.get('manual', False)
    try:
        # THIS is the actual durability guarantee, not the in-memory
        # queue below -- a row here means the signal survives a process
        # kill/restart between "accepted" and "executed" (see db.py's
        # webhook_signals comment and recover_pending_webhook_signals()
        # below). Must stay fast: one INSERT, same cost class as
        # save_trade -- everything slow (broker calls) still happens
        # only in the background.
        signal_id = db.enqueue_webhook_signal(symbol, action, manual_flag)
    except Exception as e:
        # A transient DB outage must never be the reason a real trade
        # signal gets rejected outright -- same "a DB hiccup never
        # blocks a real trade" rule this codebase already applies
        # everywhere else (e.g. save_trade). Falls back to in-memory-only
        # queuing for just this one signal: durability is degraded for
        # it specifically, but it still gets processed normally.
        logging.error(
            'Could not persist webhook signal for {} {} -- durability degraded for '
            'THIS signal only, still processing it: {}'.format(action, symbol, e)
        )
        signal_id = None
    webhook_queue.enqueue(
        symbol, lambda: _process_queued_webhook_signal(signal_id, action, symbol, manual_flag)
    )
    return jsonify({'status': 'accepted', 'symbol': symbol, 'action': action}), 202


def _process_queued_webhook_signal(signal_id, action, symbol, manual_flag):
    """Runs _process_trade_signal on webhook_queue's background worker
    thread for `symbol` -- outside any Flask request context, since
    webhook() has already returned its response by the time this runs.
    app.app_context() (not a full request context) is all that's needed:
    _process_trade_signal only touches `app` indirectly via jsonify(),
    never `request`/`session` directly -- same minimal-context pattern
    run_position_safety_checks() already uses to call this same function
    from ITS OWN background (APScheduler) thread. Its return value
    (a Flask response object) is discarded here on purpose: nothing is
    listening for it anymore, and every outcome it could represent
    (placed, rejected, errored) is already fully captured by
    _process_trade_signal's own logging before it returns.

    `signal_id` is the webhook_signals row (db.py) backing this signal's
    durability -- None if the INSERT itself failed (see webhook()
    above), in which case status tracking below is skipped entirely and
    this behaves exactly like before the durable-queue feature existed.
    Marked 'processing' right before the real work starts and 'done'
    (regardless of the trade's own outcome -- rejected/errored is still
    a normally-completed signal) or 'failed' (a genuinely unexpected
    exception, re-raised afterward) after -- see
    recover_pending_webhook_signals() for why only 'pending' rows are
    ever safe to auto-resume, and why 'processing'/'failed' are not."""
    if signal_id is not None:
        try:
            db.mark_webhook_signal_processing(signal_id)
        except Exception as e:
            logging.warning('Could not mark webhook signal {} as processing: {}'.format(signal_id, e))
    with app.app_context():
        try:
            _process_trade_signal(action, symbol, manual_flag)
        except Exception:
            if signal_id is not None:
                try:
                    db.mark_webhook_signal_failed(signal_id, traceback.format_exc())
                except Exception as e:
                    logging.warning('Could not mark webhook signal {} as failed: {}'.format(signal_id, e))
            raise  # webhook_queue's own worker loop logs this -- see there
        else:
            if signal_id is not None:
                try:
                    db.mark_webhook_signal_done(signal_id)
                except Exception as e:
                    logging.warning('Could not mark webhook signal {} as done: {}'.format(signal_id, e))


def recover_pending_webhook_signals():
    """Run once at startup (called at module import time, below) --
    resumes any webhook_signals rows left in 'pending' after a crash or
    restart cut a signal off between "durably accepted" and "actually
    executed". Ordered by id (global insertion order) and re-enqueued
    per symbol in that order, so webhook_queue.py's own per-symbol FIFO
    guarantee reproduces the original arrival order exactly.

    Deliberately does NOT touch rows left in 'processing' or 'failed':
    those started executing before the interruption, and there's no safe
    way to tell from here whether the broker call itself already fired
    -- blindly re-running risks a DUPLICATE real order, which is worse
    than one signal needing a human to check the broker/dashboard
    directly. Those get logged loudly (ERROR, so it lands in error_log)
    instead, for exactly that manual review.

    Runs synchronously at import time (NOT scheduled as a job) -- this
    must complete before gunicorn starts accepting new /webhook traffic,
    or a brand-new signal for a symbol could create that symbol's
    queue/worker and start running before an OLDER pending signal for
    the SAME symbol gets re-enqueued here, silently reordering them.
    """
    try:
        stuck = db.get_stuck_webhook_signals()
    except Exception as e:
        logging.error('Could not check for stuck webhook signals at startup: {}'.format(e))
        stuck = []
    for row in stuck:
        logging.error(
            "Webhook signal #{} ({} {}) was left in status={!r} by a previous crash/restart -- "
            "NOT auto-resumed (can't safely tell if the broker call already fired). "
            "received_at={}, error_message={!r}. Check the broker/dashboard directly and "
            "resolve manually.".format(
                row['id'], row['action'], row['symbol'], row['status'],
                row['received_at'], row.get('error_message'),
            )
        )

    try:
        pending = db.get_pending_webhook_signals()
    except Exception as e:
        logging.error('Could not check for pending webhook signals at startup: {}'.format(e))
        return
    for row in pending:
        logging.warning(
            'Resuming webhook signal #{} ({} {}) left pending by a previous crash/restart.'.format(
                row['id'], row['action'], row['symbol'],
            )
        )
        webhook_queue.enqueue(
            row['symbol'],
            lambda row=row: _process_queued_webhook_signal(
                row['id'], row['action'], row['symbol'], row['manual_flag'],
            ),
        )


recover_pending_webhook_signals()


# Params observed directly on TradingView's own alert condition strings
# (each asset class's alert shows its Pine input.*() values, e.g. stock's
# "(7, 0.05, 9, 21, 0.6, 0.35, 14, 45)") -- lookback, breakout_buffer_pct,
# ema_fast_length, ema_slow_length, take_profit_pct, stop_loss_pct,
# rsi_length, rsi_min, in that order, matching backtest.strategy.
# DEFAULT_PARAMS' key order minus use_rsi_filter (not shown numerically
# in the alert condition string -- assumed True, matching every observed
# live entry). This is INFERRED from what's observably live, not
# confirmed against the actual Pine script (this repo doesn't contain
# it) -- correct by hand via the strategy API (create_strategy +
# assign_strategy_to_symbol) if the real live params ever differ.
_OBSERVED_LIVE_STRATEGY_PARAMS = {
    "stock": {
        "lookback": 7, "breakout_buffer_pct": 0.05, "ema_fast_length": 9, "ema_slow_length": 21,
        "take_profit_pct": 0.6, "stop_loss_pct": 0.35, "use_rsi_filter": True, "rsi_length": 14, "rsi_min": 45,
    },
    "forex": {
        "lookback": 7, "breakout_buffer_pct": 0.02, "ema_fast_length": 9, "ema_slow_length": 21,
        "take_profit_pct": 0.2, "stop_loss_pct": 0.1, "use_rsi_filter": True, "rsi_length": 14, "rsi_min": 45,
    },
    "crypto": {
        "lookback": 7, "breakout_buffer_pct": 0.15, "ema_fast_length": 9, "ema_slow_length": 21,
        "take_profit_pct": 2, "stop_loss_pct": 1.2, "use_rsi_filter": True, "rsi_length": 14, "rsi_min": 45,
    },
}


def seed_default_strategies():
    """One-time bootstrap, run at startup: if NO strategy has ever been
    created (a brand-new deployment of this feature), creates one named
    strategy per asset class from _OBSERVED_LIVE_STRATEGY_PARAMS above
    and assigns every currently watched symbol to its asset class's
    strategy -- this IS the per-symbol params store Phase 0 asked for.

    Strictly a first-run bootstrap: if ANY strategy already exists
    (whether from a previous run of this function or an operator having
    created one manually since), this is a complete no-op -- it must
    never overwrite real strategy history or an operator's own edits.
    """
    try:
        if db.list_strategies():
            return
    except Exception as e:
        logging.error('Could not check for existing strategies at startup: {}'.format(e))
        return

    for asset_class, params in _OBSERVED_LIVE_STRATEGY_PARAMS.items():
        try:
            strategy = db.create_strategy(
                'Higher High Breakout - {}'.format(asset_class.capitalize()),
                params,
                description='Seeded at Phase-0 rollout from params observed live on '
                             "TradingView's alert log -- not confirmed against the Pine "
                             'script itself.',
            )
        except Exception as e:
            logging.error('Could not seed default strategy for {}: {}'.format(asset_class, e))
            continue
        for symbol in state.watched_symbols.get(asset_class, []):
            try:
                db.assign_strategy_to_symbol(symbol, strategy['id'])
            except Exception as e:
                logging.error('Could not assign default strategy to {}: {}'.format(symbol, e))


seed_default_strategies()


@app.route('/api/strategies', methods=['GET', 'POST'])
def api_strategies():
    """Session-gated (dashboard only, never TradingView). GET lists every
    strategy version ever created (db.list_strategies() -- full history,
    immutable rows, see db.py's schema comment). POST creates a NEW
    version -- there is no PUT/edit route on purpose: "editing" a
    strategy always means calling this again with the same `name` and
    different `params`, which auto-increments the version rather than
    mutating anything a trade's strategy_id might already point at."""
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401

    if request.method == 'POST':
        data = request.json or {}
        name = data.get('name')
        params = data.get('params')
        description = data.get('description')
        if not name or not params:
            return jsonify({'error': 'name and params are required'}), 400
        try:
            strategy = db.create_strategy(name, params, description)
        except Exception as e:
            logging.error('Could not create strategy {!r}: {}'.format(name, e))
            return jsonify({'error': 'failed to create strategy'}), 500
        logging.warning(
            'New strategy created: #{} {!r} v{}'.format(strategy['id'], strategy['name'], strategy['version'])
        )
        return jsonify(strategy), 201

    try:
        strategies = db.list_strategies()
    except Exception as e:
        logging.error('Could not list strategies: {}'.format(e))
        return jsonify({'error': 'failed to list strategies'}), 500
    return jsonify({'strategies': strategies})


@app.route('/api/strategies/assignments', methods=['GET'])
def api_strategy_assignments():
    """Session-gated. Every symbol's currently active strategy (full
    joined rows -- id/name/version/params, not just the bare id) -- what
    the dashboard's strategy-switch UI would list, and what /webhook's
    strategy_id check validates every non-legacy signal against."""
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401
    try:
        assignments = db.get_all_symbol_strategy_assignments()
    except Exception as e:
        logging.error('Could not list strategy assignments: {}'.format(e))
        return jsonify({'error': 'failed to list assignments'}), 500
    return jsonify({'assignments': assignments})


@app.route('/api/strategies/assign', methods=['POST'])
def api_assign_strategy():
    """Switches `symbol`'s active strategy -- session-gated, dashboard-
    initiated, deliberately SYNCHRONOUS (same reasoning as
    /api/manual_close: the caller is actively waiting on the real
    outcome to render, not an external service with its own delivery
    timeout -- see webhook()'s docstring for why THAT route is async and
    this one isn't).

    Force-closes any open position for `symbol` BEFORE updating the
    assignment -- Eli's explicit product decision: force-close on
    switch, not "let the old strategy's exit logic keep running until it
    closes naturally". The order matters, not just the fact of it:
    closing FIRST, while the OLD assignment is still active, means the
    close's own trade_log entry correctly records which strategy the
    position being closed was entered under (source='strategy_switch',
    a distinct tag from manual_close/safety_stop_loss -- see
    trade_explanations.explain_exit). If the close fails, the assignment
    is NOT updated -- force-close-on-switch is a guarantee, not
    best-effort; a symbol must never end up newly assigned while a
    position from the OLD strategy is still open and unaccounted for.
    """
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401
    data = request.json or {}
    symbol = data.get('symbol')
    new_strategy_id = data.get('strategy_id')
    if not symbol or not new_strategy_id:
        return jsonify({'error': 'symbol and strategy_id are required'}), 400

    try:
        new_strategy = db.get_strategy(new_strategy_id)
    except Exception as e:
        logging.error('Could not look up strategy {}: {}'.format(new_strategy_id, e))
        return jsonify({'error': 'failed to look up strategy'}), 500
    if new_strategy is None:
        return jsonify({'error': 'no such strategy: {}'.format(new_strategy_id)}), 404

    position = next((p for p in get_all_positions() if p['symbol'] == symbol), None)
    closed_position = False
    if position is not None:
        close_action = 'sell' if position['direction'] == 'long' else 'buy'
        logging.warning(
            'STRATEGY SWITCH for {}: force-closing open {} {} position ({} units) before switching '
            'to strategy #{} ({} v{}).'.format(
                symbol, position['direction'], position['asset_class'], position['qty'],
                new_strategy_id, new_strategy['name'], new_strategy['version'],
            )
        )
        # Routed through webhook_queue (same per-symbol queue /webhook
        # uses) rather than calling _process_trade_signal directly --
        # see webhook_queue.py's module docstring for why all four
        # force-close-capable paths now go through it.
        try:
            result = webhook_queue.enqueue_and_wait(
                symbol,
                lambda: _call_process_trade_signal_in_context(
                    close_action, symbol, is_manual=True, source='strategy_switch', force_close_qty=position['qty'],
                ),
            )
        except concurrent.futures.TimeoutError:
            logging.error(
                'STRATEGY SWITCH for {} ABORTED: timed out waiting in the queue -- assignment NOT '
                'changed. The close attempt is still queued and will eventually run.'.format(symbol)
            )
            return jsonify({'error': 'timed out waiting for the close to process -- strategy NOT switched'}), 504
        status_code = result[1] if isinstance(result, tuple) else 200
        if status_code != 200:
            logging.error(
                'STRATEGY SWITCH for {} ABORTED: force-close did not succeed (status {}) -- '
                'assignment NOT changed.'.format(symbol, status_code)
            )
            return jsonify({'error': 'could not close the open position -- strategy NOT switched'}), 502
        closed_position = True

    try:
        db.assign_strategy_to_symbol(symbol, new_strategy_id)
    except Exception as e:
        logging.error('Could not assign strategy {} to {}: {}'.format(new_strategy_id, symbol, e))
        return jsonify({
            'error': 'position closed but the assignment failed to save -- retry the assignment', 'closed_position': closed_position,
        }), 500

    return jsonify({
        'status': 'assigned', 'symbol': symbol, 'strategy_id': new_strategy_id,
        'strategy_name': new_strategy['name'], 'strategy_version': new_strategy['version'],
        'closed_position': closed_position,
    })


@app.route('/api/manual_trade', methods=['POST'])
def api_manual_trade():
    """SPA-facing route for the dashboard's manual buy/sell buttons — gated
    by the logged-in session instead, so the webhook secret never has to
    reach the browser at all (the old Jinja dashboard used to embed it in
    the page for this exact purpose)."""
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401
    data = request.json or {}
    action = data.get('action')
    symbol = data.get('symbol')
    if not action or not symbol:
        return jsonify({'error': 'missing fields'}), 400
    return _process_trade_signal(action, symbol, is_manual=True, source='manual')


@app.route('/api/manual_close', methods=['POST'])
def api_manual_close():
    """SPA-facing route for the dashboard's per-position "Close" button
    (session-gated, same as /api/manual_trade above). Looks up
    `symbol`'s CURRENT real position (get_all_positions(), the same
    source of truth run_position_safety_checks() and the dashboard's own
    positions table use) and closes exactly that quantity via the same
    force-close path the safety net uses -- _process_trade_signal with
    force_close_qty set -- rather than re-deriving a size from the risk
    formula. Direction-aware like run_position_safety_checks(): closing a
    forex SHORT means buying it back, not selling further.

    Rejects with 400 if `symbol` has no open position right now (the
    frontend already only shows this button for symbols with one, but a
    position can close on its own — e.g. a TradingView exit landing —
    between page load and the click, so this is a real check, not
    defensive dead code).

    Also rejects with 400 if the position's qty_available is less than
    its full qty -- meaning some (or all) of it is already tied up in
    another open, unfilled order (most commonly: an earlier click on
    this exact button submitted a "day" order that's still queued
    because the market was closed at the time, and hasn't filled yet).
    Submitting a second close in that state doesn't get ignored or
    queued -- Alpaca rejects it outright with a generic "insufficient
    funds" error that has nothing to do with account funds and reads
    like a broken feature. Checking first turns that into an accurate,
    actionable message instead.

    Logged as a WARNING (not just the normal INFO trade-summary line
    every path already gets) specifically so this lands in error_log
    distinctly tagged as an operator-initiated override -- mirrors
    run_position_safety_checks()'s own logging.error(...) call
    immediately before ITS force-close, for the same reason: a full
    audit trail of every force-close needs to say WHY, not just what."""
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401
    data = request.json or {}
    symbol = data.get('symbol')
    if not symbol:
        return jsonify({'error': 'missing symbol'}), 400

    position = next((p for p in get_all_positions() if p['symbol'] == symbol), None)
    if position is None:
        return jsonify({'error': 'no open position for {} (nothing to close)'.format(symbol)}), 400

    qty_available = position.get('qty_available', position['qty'])
    if qty_available < position['qty']:
        logging.warning(
            'MANUAL CLOSE blocked for {} {}: only {} of {} units are available to close right now -- '
            'the rest is already tied up in another open order (e.g. an earlier close still queued '
            'because the market was closed) or not yet settled.'.format(
                position['asset_class'], symbol, qty_available, position['qty'],
            )
        )
        return jsonify({
            'error': '{} already has an order pending on part or all of this position -- wait for it '
                     'to fill (or cancel it) before requesting another close.'.format(symbol)
        }), 400

    close_action = 'sell' if position['direction'] == 'long' else 'buy'
    logging.warning(
        'MANUAL CLOSE requested via dashboard: {} {} {} {} units (direction={}, unrealized_pl={:.2f}) '
        '-- operator-initiated, not a strategy or safety-net exit.'.format(
            close_action, position['asset_class'], symbol, position['qty'],
            position['direction'], position['unrealized_pl'],
        )
    )
    # Routed through webhook_queue (same per-symbol queue /webhook uses)
    # rather than calling _process_trade_signal directly -- closes the
    # gap where a dashboard "Close" click and a webhook signal for the
    # same symbol landing at nearly the same instant could otherwise
    # race each other placing two orders concurrently. See
    # webhook_queue.py's module docstring.
    try:
        return webhook_queue.enqueue_and_wait(
            symbol,
            lambda: _call_process_trade_signal_in_context(
                close_action, symbol, is_manual=True, source='manual_close', force_close_qty=position['qty'],
            ),
        )
    except concurrent.futures.TimeoutError:
        logging.error('MANUAL CLOSE for {} timed out waiting in the queue -- it is still queued and will '
                       'eventually run; check trade history/positions directly.'.format(symbol))
        return jsonify({'error': 'timed out waiting for the close to process -- check positions directly'}), 504


def _process_trade_signal(action, symbol, is_manual, source='webhook', force_close_qty=None):
    """Shared by /webhook (TradingView, secret-gated), /api/manual_trade
    (dashboard buttons, session-gated), /api/manual_close (the
    dashboard's per-position "Close" button, also session-gated), AND
    run_position_safety_checks() (the automatic stop-loss monitor) --
    sizes, risk checks, executes, and logs a trade signal identically
    regardless of where it came from.

    `source` is persisted on the trade for the UI to show ('webhook',
    'manual', 'manual_close', or 'safety_stop_loss') -- distinguishing a
    normal strategy exit, an operator's manual override, and an
    emergency one matters, don't let it get lost.

    `force_close_qty`, when set, means "close exactly this much, right
    now" (used by the safety-net monitor AND /api/manual_close): skips
    bot_enabled/max-trades/dedup entirely (an emergency exit -- or an
    operator explicitly asking to exit right now -- must never be
    blocked by them) and skips the normal per-asset-class sizing math in
    favor of using the exact currently-held quantity.
    """
    check_daily_rollover()

    asset_class = asset_class_for_symbol(symbol)
    broker = BROKERS[asset_class]
    is_forced_close = force_close_qty is not None

    # Fetched once, up front, reused twice below: (1) so
    # _sanity_check_signal compares against the symbol's ACTUALLY
    # configured params (Phase 0's per-symbol store) rather than always
    # the generic default, and (2) to generate an accurate trade
    # explanation later (trade_explanations.py). A DB miss (no
    # assignment yet -- e.g. a symbol added after Phase 0's bootstrap
    # ran, or a transient DB hiccup) falls back to
    # STRATEGY_DEFAULT_PARAMS, same as this function's behavior before
    # per-symbol assignment existed -- never blocks a trade over this.
    try:
        strategy_assignment = db.get_symbol_strategy_assignment(symbol)
    except Exception as e:
        logging.warning('Could not look up strategy assignment for {}: {}'.format(symbol, e))
        strategy_assignment = None
    strategy_params = strategy_assignment['params'] if strategy_assignment else STRATEGY_DEFAULT_PARAMS
    strategy_id = strategy_assignment['id'] if strategy_assignment else None

    if not state.bot_enabled and not is_manual and not is_forced_close:
        logging.warning('Rejected {} {} {}: bot paused'.format(action, asset_class, symbol))
        return jsonify({'error': 'bot paused'}), 400
    if state.trades_today[asset_class] >= state.max_trades_per_day[asset_class] and not is_manual and not is_forced_close:
        logging.warning('Rejected {} {} {}: max trades/day reached ({})'.format(action, asset_class, symbol, state.max_trades_per_day[asset_class]))
        return jsonify({'error': 'max {} trades reached for today'.format(asset_class)}), 400

    signal_key = '{0}_{1}'.format(symbol, action)
    now = time.time()
    if not is_manual and not is_forced_close:
        last = state.last_signal_time.get(signal_key)
        if last is not None and now - last < 60:
            logging.info(
                'Dropped duplicate {} {} {}: identical signal {:.1f}s ago already executed '
                'or is still executing (60s dedup window)'.format(action, asset_class, symbol, now - last)
            )
            return jsonify({'status': 'duplicate ignored'}), 200
    # Stamped BEFORE execution, not after: gunicorn runs threads=8, and two
    # near-simultaneous identical webhooks must not both pass the check
    # above and both place an order. The flip side is that at this point
    # the stamp only means "attempted", not "executed" -- so every failure
    # path below has to roll it back (the finally clause at the bottom),
    # or a TradingView retry of the same alert after a failed first
    # attempt gets silently swallowed as a duplicate for the full 60s
    # window with the signal never actually traded.
    state.last_signal_time[signal_key] = now

    sanity_signal = None
    if not is_manual and not is_forced_close:
        sanity_signal = _sanity_check_signal(broker, symbol, asset_class, action, strategy_params)

    order_placed = False
    try:
        account = broker.get_account_info()
        price = broker.get_price(symbol)
        risk_amount = account['equity'] * (state.risk_percent[asset_class] / 100.0)

        # Defensively clamp to the cap rather than let a drifted
        # risk_percent silently reject every trade for this asset class
        # -- this is exactly the failure mode that caused a real 2-day
        # forex outage (risk_percent had drifted above the cap; every
        # trade got rejected with no obvious reason until the sizing
        # knob was manually lowered). Sizing down to the cap and logging
        # it is strictly better than rejecting outright: the trade still
        # happens, just smaller than requested, and it's visible in the
        # logs instead of silent.
        max_position_value = account['equity'] * state.risk_caps[asset_class]['max_position_size_pct']
        if risk_amount > max_position_value:
            logging.warning(
                'Clamping {} {} position size: risk_percent ({}%) implies ${:.2f} but the cap allows ${:.2f} -- sizing to the cap instead of rejecting.'.format(
                    asset_class, symbol, state.risk_percent[asset_class], risk_amount, max_position_value,
                )
            )
            risk_amount = max_position_value

        reduces_position = False

        if is_forced_close:
            size = force_close_qty
            reduces_position = True
        elif action == 'sell' and asset_class in ('stock', 'crypto'):
            # Alpaca is spot-only for crypto (no shorting), and we don't
            # want naked shorts on stock either -- a sell should close
            # whatever the bot's REAL account actually holds, not
            # re-derive a fresh size from the risk formula (which can
            # easily exceed, or have nothing to do with, the real
            # position). Without this check, a sell signal firing when
            # the bot doesn't actually hold anything -- e.g. because an
            # earlier buy silently failed and TradingView's OWN strategy
            # simulation drifted out of sync with the real account --
            # sends a doomed order straight to the broker instead of
            # failing with a clear reason.
            held_qty = _get_held_qty(broker, symbol)
            if held_qty <= 0:
                logging.warning('Rejected sell {} {}: bot holds no position to sell'.format(asset_class, symbol))
                return jsonify({'error': 'no position to sell for {} (bot holds none)'.format(symbol)}), 400
            size = held_qty
            reduces_position = True
        elif asset_class == 'stock':
            size = int(risk_amount / price)
            if size < 1:
                logging.warning('Rejected {} stock {}: position too small (risk_amount={:.2f}, price={:.2f})'.format(action, symbol, risk_amount, price))
                return jsonify({'error': 'position too small'}), 400
        elif asset_class == 'crypto':
            # Crypto supports fractional quantities (Alpaca allows down to
            # 1e-6). Round DOWN (floor), not to-nearest — rounding up even
            # a fraction of a unit can push position_value a cent or two
            # over the risk manager's cap, causing a correctly-sized trade
            # to get rejected right at the boundary.
            size = math.floor((risk_amount / price) * 1_000_000) / 1_000_000
            if size <= 0:
                logging.warning('Rejected {} crypto {}: position too small (risk_amount={:.2f}, price={:.2f})'.format(action, symbol, risk_amount, price))
                return jsonify({'error': 'position too small'}), 400
        else:
            # Forex units = risk_amount / price (same principle as stock
            # shares and crypto quantity). Floor to a whole unit — forex
            # position sizes are conventionally whole units, and flooring
            # (not rounding-to-nearest) guarantees position_value never
            # exceeds the risk manager's cap due to rounding.
            #
            # NOTE: this still doesn't account for pip value or standard
            # lot-size conventions (micro/mini/standard lots) — it treats
            # 1 unit as 1 unit of the base currency. Fine for testing;
            # revisit before sizing real forex positions for live trading.
            size = math.floor(risk_amount / price)
            if size < 1:
                logging.warning('Rejected {} forex {}: position too small (risk_amount={:.2f}, price={:.2f})'.format(action, symbol, risk_amount, price))
                return jsonify({'error': 'position too small'}), 400

        approved, reason = risk_manager.check_trade(
            broker, symbol, action, size, asset_class, price=price, reduces_position=reduces_position,
        )
        if not approved:
            logging.warning('Rejected {} {} {}: {}'.format(action, asset_class, symbol, reason))
            return jsonify({'error': reason}), 400

        # pnl is only computable when this trade is CLOSING something:
        # a 'sell' closing a long looks up the last 'buy' as its entry
        # (long pnl = exit - entry); a 'buy' closing a short (forex only,
        # since stock/crypto never short here -- see get_all_positions'
        # direction field) looks up the last 'sell' as its entry instead
        # (short pnl = entry - exit, since profit comes from price
        # falling). An opening trade (not reduces_position) has no entry
        # reference yet, so pnl stays None until it's eventually closed.
        pnl = None
        if reduces_position:
            entry_action = 'buy' if action == 'sell' else 'sell'
            entry_trade = next(
                (t for t in reversed(state.trade_log)
                 if t['action'] == entry_action and t['symbol'] == symbol and t['asset_class'] == asset_class),
                None
            )
            if entry_trade:
                entry_price = float(entry_trade['price'])
                pnl = round((price - entry_price) * size, 2) if action == 'sell' else round((entry_price - price) * size, 2)

        broker.place_order(symbol, action, size)
        order_placed = True
        log_prefix = (
            'SAFETY-NET FORCED CLOSE: ' if source == 'safety_stop_loss'
            else 'MANUAL CLOSE: ' if source == 'manual_close'
            else 'STRATEGY SWITCH FORCED CLOSE: ' if source == 'strategy_switch'
            else ''
        )
        logging.info('{}{} {} {} of {} ({})'.format(log_prefix, action.upper(), size, asset_class, symbol, config.TRADING_MODE))

        # Attach whatever regime tag we most recently computed for this
        # symbol (from the scheduled classifier) — a DB miss just means
        # no regime data exists yet for a brand-new symbol, not an error.
        trade_regime = None
        try:
            latest = db.get_latest_regime(symbol)
            if latest:
                trade_regime = latest['regime']
        except Exception as e:
            logging.warning('Could not look up regime for {}: {}'.format(symbol, e))

        # Human-readable rationale (trade_explanations.py) -- generated
        # AFTER the order already placed, same "never let a side
        # computation risk looking like the trade itself failed" rule as
        # the regime lookup right above. Never blocks/delays the trade.
        explanation = None
        try:
            if reduces_position:
                explanation = trade_explanations.explain_exit(
                    action, symbol, asset_class, price, source, entry_trade=entry_trade,
                    params=strategy_params, broker=broker,
                    timeframe=_REGIME_TIMEFRAMES.get(asset_class, '1h'),
                )
            else:
                # A forex 'sell' with reduces_position False means our own
                # sizing logic treated it as a fresh entry (see this
                # function's docstring on reduces_position/pnl above) --
                # OANDA nets positions itself, so this MAY be economically
                # closing an existing long, but from THIS function's own
                # classification it's an entry, and the explanation follows
                # that same classification rather than second-guessing it.
                is_short = action == 'sell' and asset_class == 'forex'
                signal = sanity_signal
                if signal is None:
                    # No sanity check ran for this path (manual, forced,
                    # or a short entry _sanity_check_signal's buy/sell
                    # comparison doesn't apply to) -- compute fresh,
                    # best-effort, just for the explanation.
                    signal = _compute_current_signal(broker, symbol, asset_class, strategy_params)

                # Candlestick/Fibonacci pattern read (patterns.py) --
                # purely additive supporting context alongside the
                # breakout/EMA/RSI rationale above, never a substitute
                # for it. A SEPARATE broker fetch from the one behind
                # `signal` (that one only returns the latest bar's
                # computed values, not the raw bars patterns.py needs) --
                # fine to spend the extra round-trip here since this
                # entire block already runs on webhook_queue's background
                # worker thread, off the synchronous /webhook response
                # path (see server.py's webhook() docstring).
                detected_patterns = None
                try:
                    pattern_bars = broker.get_ohlcv(
                        symbol, timeframe=_REGIME_TIMEFRAMES.get(asset_class, '1h'), limit=30,
                    )
                    detected_patterns = patterns.analyze_patterns(pattern_bars)
                except Exception as e:
                    logging.warning('Could not compute patterns for {}: {}'.format(symbol, e))

                explanation = trade_explanations.explain_entry(
                    action, symbol, asset_class, price, signal, strategy_params,
                    is_manual=is_manual, is_short=is_short, detected_patterns=detected_patterns,
                )
        except Exception as e:
            logging.warning('Could not generate trade explanation for {} {} {}: {}'.format(action, asset_class, symbol, e))

        state.trades_today[asset_class] += 1
        price_str = (
            '{:.5f}'.format(price) if asset_class == 'forex'
            else '{:.4f}'.format(price) if asset_class == 'crypto'
            else '{:.2f}'.format(price)
        )
        state.trade_log.append({
            'time': datetime.datetime.now().isoformat(),  # full datetime, not just time-of-day -- see load_persisted_state's matching format
            'action': action,
            'symbol': symbol,
            'asset_class': asset_class,
            'qty': size,
            'price': price_str,
            'pnl': pnl,
            'regime': trade_regime,
            'source': source,
            'explanation': explanation,
            'strategy_id': strategy_id,
        })
        try:
            db.save_trade(
                action, symbol, asset_class, size, price, pnl, regime=trade_regime, source=source,
                explanation=explanation, strategy_id=strategy_id,
            )
        except Exception as e:
            # Trade already executed on the broker — a DB hiccup here should
            # never look like the trade itself failed. Log and move on.
            logging.error('Trade succeeded but failed to persist to DB: {}'.format(e))

        if pnl is not None:
            try:
                risk_manager.record_trade_result(asset_class, pnl, get_combined_equity())
            except BrokerConnectionError:
                pass

        return jsonify({'status': 'order placed', 'qty': size, 'symbol': symbol, 'asset_class': asset_class})

    except InsufficientFundsError as e:
        logging.warning('Rejected {} {} {}: {}'.format(action, asset_class, symbol, e))
        return jsonify({'error': str(e)}), 400
    except MarketClosedError as e:
        logging.warning('Rejected {} {} {}: {}'.format(action, asset_class, symbol, e))
        return jsonify({'error': str(e)}), 400
    except InvalidSymbolError as e:
        logging.warning('Rejected {} {} {}: {}'.format(action, asset_class, symbol, e))
        return jsonify({'error': str(e)}), 400
    except BrokerConnectionError as e:
        logging.error('Broker error on {} {} {}: {}'.format(action, asset_class, symbol, e))
        alerts.record_broker_error(detail=traceback.format_exc())
        return jsonify({'error': str(e)}), 502
    finally:
        # The dedup stamp above is only allowed to mean "this signal
        # already executed" once the order actually reached the broker
        # and place_order returned normally. On any other outcome --
        # sizing rejection, no position to sell, risk manager rejection,
        # any of the broker/market errors above -- remove OUR stamp so a
        # genuine retry isn't silently dropped as a duplicate. Compare
        # against our own timestamp first so a newer stamp written by
        # another thread is never clobbered.
        if not order_placed and state.last_signal_time.get(signal_key) == now:
            state.last_signal_time.pop(signal_key, None)
            logging.info(
                'Cleared dedup stamp for {} {}: attempt did not execute, a retry within 60s will be accepted'.format(
                    action, symbol
                )
            )



def _compute_live_performance():
    """Same win-rate/max-drawdown/Sharpe methodology the backtester uses
    (backtest.metrics.compute_metrics), computed from real closed trades
    instead of a simulation -- lets the Backtest page show 'here's what
    the strategy predicted' next to 'here's what actually happened' in
    directly comparable terms.

    Scoped to whatever's in state.trade_log (persisted trades are capped
    at the most recent 200 by load_persisted_state -- see server.py/db.py),
    not the account's full history since inception. Labeled as such in
    the response rather than implying a longer track record than we
    actually have data for.
    """
    closed = [t for t in state.trade_log if t.get('pnl') is not None]
    if not closed:
        return {
            'overall': {'trade_count': 0, 'win_rate_pct': None, 'max_drawdown_pct': None, 'sharpe_ratio': None, 'total_pnl_abs': 0.0, 'avg_pnl_pct': None},
            'by_regime': {},
            'trade_count': 0,
            'window_note': 'No closed trades yet.',
        }

    tagged_trades = []
    for t in closed:
        pnl_abs = float(t['pnl'])
        qty = float(t['qty'])
        price = float(t['price'])
        proceeds = qty * price
        cost_basis = proceeds - pnl_abs  # exact, not approximated -- see _process_trade_signal's pnl = (exit - entry) * size
        pnl_pct = (pnl_abs / cost_basis * 100) if cost_basis > 0 else 0.0
        tagged_trades.append({
            'pnl_abs': pnl_abs,
            'pnl_pct': pnl_pct,
            'regime': t.get('regime') or 'unknown',
        })

    # Back into an implied starting capital (equity right before the
    # oldest trade in this window) rather than assuming a fixed nominal
    # value -- max drawdown % is meaningless without a real baseline.
    total_realized = sum(t['pnl_abs'] for t in tagged_trades)
    try:
        current_equity = get_combined_equity()
        initial_capital = current_equity - total_realized
    except BrokerConnectionError:
        initial_capital = state.equity_history['values'][0] if state.equity_history['values'] else 10000.0

    metrics = compute_metrics(tagged_trades, initial_capital=initial_capital)
    metrics['trade_count'] = len(tagged_trades)
    metrics['initial_capital'] = round(initial_capital, 2)
    metrics['window_note'] = 'Based on the last {} closed trades kept in the trade log.'.format(len(tagged_trades))
    return metrics


@app.route('/api/backtest')
def api_backtest():
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401

    try:
        live_performance = _compute_live_performance()
    except Exception as e:
        logging.warning('Could not compute live performance: {}'.format(e))
        live_performance = None

    results_path = os.path.join(os.path.dirname(__file__), 'backtest_results.json')
    if not os.path.exists(results_path):
        return jsonify({'results': None, 'generated_at': None, 'live_performance': live_performance})

    with open(results_path) as f:
        results = json.load(f)

    generated_at = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(results_path)))
    return jsonify({'results': results, 'generated_at': generated_at, 'live_performance': live_performance})


@app.route('/ui/layout', methods=['GET', 'POST'])
def ui_layout():
    """Persists the dashboard's widget grid layout (positions/sizes per
    widget id) across devices and redeploys — same bot_settings key-value
    store everything else in Settings already uses, just a new key."""
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401

    if request.method == 'POST':
        layout = request.json or {}
        try:
            db.save_setting('ui_layout', layout)
        except Exception as e:
            logging.warning('Could not persist ui_layout: {}'.format(e))
            return jsonify({'error': 'failed to save layout'}), 500
        return jsonify({'status': 'saved'})

    try:
        layout = db.get_setting('ui_layout', default=None)
    except Exception as e:
        logging.warning('Could not load ui_layout: {}'.format(e))
        layout = None
    return jsonify({'layout': layout})


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'running', 'mode': config.TRADING_MODE})


# --- React SPA catch-all -------------------------------------------------
# Everything above is /api, /ui, /webhook, /health — real endpoints. Any
# other path is either a real static asset (JS/CSS bundle) or a
# client-side route (React Router) — this one route handles both: serve
# the file if it exists in frontend/dist, otherwise fall back to
# index.html and let the browser's JS take over routing.
_FRONTEND_DIST = os.path.join(os.path.dirname(__file__), 'frontend', 'dist')


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_spa(path):
    full_path = os.path.join(_FRONTEND_DIST, path)
    if path and os.path.isfile(full_path):
        return send_from_directory(_FRONTEND_DIST, path)
    index_path = os.path.join(_FRONTEND_DIST, 'index.html')
    if not os.path.exists(index_path):
        return jsonify({
            'error': 'Frontend not built yet. Run `npm run build` in frontend/.'
        }), 503
    return send_from_directory(_FRONTEND_DIST, 'index.html')


if __name__ == '__main__':
    import os
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
