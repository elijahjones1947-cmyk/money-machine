from flask import Flask, request, jsonify, session, send_from_directory
import logging, time, datetime, math

import config
import state
import db
import regime
from apscheduler.schedulers.background import BackgroundScheduler
from errors import InsufficientFundsError, MarketClosedError, InvalidSymbolError, BrokerConnectionError
from brokers.alpaca_broker import AlpacaBroker
from brokers.oanda_broker import OandaBroker
from risk.risk_manager import RiskManager
from hermes import hermes_bp, init_hermes
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

risk_manager = RiskManager(config.get_risk_config())

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
# Matches each asset class's alert timeframe from TradingView: 1h for
# stock/crypto, 15m for forex. Runs independently of trading itself — a
# failure here should never affect order placement, only logging quality.
_REGIME_TIMEFRAMES = {"stock": "1h", "forex": "15m", "crypto": "1h"}


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


scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(run_regime_checks, "interval", minutes=15, next_run_time=None)
scheduler.start()
# --- end market regime classifier scheduler ------------------------------


def asset_class_for_symbol(symbol):
    """Crypto pairs use Alpaca's slash format, e.g. BTC/USD.
    Forex pairs use OANDA's underscore format, e.g. EUR_USD.
    Anything else (AAPL, TSLA, ...) is treated as a stock/Alpaca symbol."""
    if "/" in symbol:
        return "crypto"
    if "_" in symbol:
        return "forex"
    return "stock"


def _get_held_qty(broker, symbol):
    """Current held quantity for `symbol` on an Alpaca-backed broker
    (stock/crypto), or 0.0 if the bot doesn't currently hold a position
    in it. Used to gate sells to what's actually held -- see
    _process_trade_signal."""
    try:
        for p in broker.get_positions():
            if p.symbol == symbol:
                return float(p.qty)
    except BrokerConnectionError as e:
        logging.warning("Could not check held position for {}: {}".format(symbol, e))
    return 0.0


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
    if data.get('password') == DASHBOARD_PASSWORD:
        session['auth'] = True
        return jsonify({'status': 'ok'})
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

    positions = []
    try:
        for p in alpaca_broker.get_positions():
            # alpaca_broker.get_positions() returns BOTH stocks and crypto
            # (same account) — split them back out by symbol format so the
            # dashboard tags each correctly instead of lumping crypto in as 'stock'.
            ac = asset_class_for_symbol(p.symbol)
            price_fmt = '{:.4f}' if ac == 'crypto' else '{:.2f}'
            positions.append({
                'symbol': p.symbol, 'qty': p.qty, 'asset_class': ac,
                'avg_entry': price_fmt.format(float(p.avg_entry_price)),
                'current_price': price_fmt.format(float(p.current_price)),
                'unrealized_pl': round(float(p.unrealized_pl), 2),
            })
    except BrokerConnectionError:
        pass

    try:
        for p in oanda_broker.get_positions():
            long_units = float(p.get('long', {}).get('units', 0))
            short_units = float(p.get('short', {}).get('units', 0))
            units = long_units if long_units != 0 else short_units
            avg_price = p.get('long', {}).get('averagePrice') if long_units != 0 else p.get('short', {}).get('averagePrice')
            unrealized = float(p.get('long', {}).get('unrealizedPL', 0)) + float(p.get('short', {}).get('unrealizedPL', 0))
            positions.append({
                'symbol': p['instrument'], 'qty': units, 'asset_class': 'forex',
                'avg_entry': '{:.5f}'.format(float(avg_price or 0)),
                'current_price': '—',
                'unrealized_pl': round(unrealized, 2),
            })
    except BrokerConnectionError:
        pass

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
        'risk_caps': config.get_risk_config(),
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


@app.route('/webhook', methods=['POST'])
def webhook():
    """External-facing route for TradingView alerts — requires the shared
    WEBHOOK_SECRET, never exposed to the browser."""
    data = request.json

    # Log every inbound webhook call regardless of outcome (secret
    # redacted) -- this is the only way to see what TradingView actually
    # sent when a failure never makes it into _process_trade_signal's
    # own rejection logging below (e.g. malformed JSON, missing fields).
    safe_data = dict(data) if data else data
    if safe_data and 'secret' in safe_data:
        safe_data['secret'] = 'REDACTED'
    logging.info('Webhook received: {}'.format(safe_data))

    if not data:
        logging.warning('Rejected webhook call: no JSON body')
        return jsonify({'error': 'no data'}), 415
    if data.get('secret') != WEBHOOK_SECRET:
        logging.warning('Rejected webhook call: bad secret')
        return jsonify({'error': 'unauthorized'}), 401

    action = data.get('action')
    symbol = data.get('symbol')
    if not action or not symbol:
        logging.warning('Rejected webhook call: missing fields (action={!r}, symbol={!r})'.format(action, symbol))
        return jsonify({'error': 'missing fields'}), 400
    if symbol in ('{{TICKER}}', '{{ticker}}'):
        logging.warning('Rejected webhook call: unsubstituted symbol placeholder')
        return jsonify({'error': 'invalid symbol'}), 400

    return _process_trade_signal(action, symbol, data.get('manual', False))


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
    return _process_trade_signal(action, symbol, is_manual=True)


def _process_trade_signal(action, symbol, is_manual):
    """Shared by both /webhook (TradingView, secret-gated) and
    /api/manual_trade (dashboard buttons, session-gated) — sizes, risk
    checks, executes, and logs a trade signal identically regardless of
    where it came from."""
    check_daily_rollover()

    asset_class = asset_class_for_symbol(symbol)
    broker = BROKERS[asset_class]

    if not state.bot_enabled and not is_manual:
        logging.warning('Rejected {} {} {}: bot paused'.format(action, asset_class, symbol))
        return jsonify({'error': 'bot paused'}), 400
    if state.trades_today[asset_class] >= state.max_trades_per_day[asset_class] and not is_manual:
        logging.warning('Rejected {} {} {}: max trades/day reached ({})'.format(action, asset_class, symbol, state.max_trades_per_day[asset_class]))
        return jsonify({'error': 'max {} trades reached for today'.format(asset_class)}), 400

    signal_key = '{0}_{1}'.format(symbol, action)
    now = time.time()
    if not is_manual:
        if signal_key in state.last_signal_time:
            if now - state.last_signal_time[signal_key] < 60:
                return jsonify({'status': 'duplicate ignored'}), 200
    state.last_signal_time[signal_key] = now

    try:
        account = broker.get_account_info()
        price = broker.get_price(symbol)
        risk_amount = account['equity'] * (state.risk_percent[asset_class] / 100.0)

        if action == 'sell' and asset_class in ('stock', 'crypto'):
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

        approved, reason = risk_manager.check_trade(broker, symbol, action, size, asset_class, price=price)
        if not approved:
            logging.warning('Rejected {} {} {}: {}'.format(action, asset_class, symbol, reason))
            return jsonify({'error': reason}), 400

        pnl = None
        if action == 'sell':
            last_buy = next(
                (t for t in reversed(state.trade_log)
                 if t['action'] == 'buy' and t['symbol'] == symbol and t['asset_class'] == asset_class),
                None
            )
            if last_buy:
                pnl = round((price - float(last_buy['price'])) * size, 2)

        broker.place_order(symbol, action, size)
        logging.info('{} {} {} of {} ({})'.format(action.upper(), size, asset_class, symbol, config.TRADING_MODE))

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
        })
        try:
            db.save_trade(action, symbol, asset_class, size, price, pnl, regime=trade_regime)
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
        return jsonify({'error': str(e)}), 502



@app.route('/api/backtest')
def api_backtest():
    if not session.get('auth'):
        return jsonify({'error': 'unauthorized'}), 401

    results_path = os.path.join(os.path.dirname(__file__), 'backtest_results.json')
    if not os.path.exists(results_path):
        return jsonify({'results': None, 'generated_at': None})

    with open(results_path) as f:
        results = json.load(f)

    generated_at = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(results_path)))
    return jsonify({'results': results, 'generated_at': generated_at})


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
