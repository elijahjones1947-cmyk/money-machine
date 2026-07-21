"""
Test suite scope, read this before adding more tests:

Covered: risk/risk_manager.py (the exact code that's caused two real
production incidents this build), regime.py's classifier math, backtest/
strategy.py's signal computation (shared by the backtester AND the live
signal sanity-check), backtest/metrics.py (shared by the backtest
results AND the live-performance section), and -- as of this file's
fixtures below -- server.py's Flask routes.

The pure-logic modules (risk_manager/regime/strategy/metrics) have no
Flask/DB/broker dependency, so their tests import them directly with no
fixtures needed.

server.py's routes are different: server.py constructs real broker
clients and calls db.init_pool()/db.init_schema() at MODULE IMPORT TIME,
which used to mean importing server.py at all required a reachable
DATABASE_URL (psycopg2.pool.SimpleConnectionPool eagerly opens minconn
connections in its constructor). db.init_pool() is now idempotent --
see its docstring in db.py -- so a test process can inject a fake
db._pool BEFORE server.py is ever imported, and server.py's own
db.init_pool() call just sees a pool already present and no-ops. That's
what the fake pool below does, entirely in-process (no real Postgres,
no mocking of db.py's own functions -- save_trade/get_setting/etc. run
their real code against a fake cursor that recognizes each literal SQL
statement db.py issues and returns canned data for it).

Real broker network calls (Alpaca/OANDA) are avoided the same way route
tests avoid the DB: server.py's already-constructed alpaca_broker/
oanda_broker instances get their BrokerInterface methods replaced with
safe in-memory fakes once, right after import -- see app_module() below.
"""

import copy
import os
from datetime import datetime, timezone

import pytest

import db

# --- Env vars config.py's require_env()/get_broker_credentials() need
# at import time -- forced (not setdefault) so route tests are
# deterministic regardless of what's in the ambient shell environment.
# None of this touches a real broker or a real Postgres instance (see
# the fake pool and fake broker methods below), so these values never
# need to be anything but fixed test constants.
TEST_WEBHOOK_SECRET = "test-webhook-secret"
TEST_DASHBOARD_PASSWORD = "test-dashboard-password"
os.environ["WEBHOOK_SECRET"] = TEST_WEBHOOK_SECRET
os.environ["DASHBOARD_PASSWORD"] = TEST_DASHBOARD_PASSWORD
os.environ["FLASK_SECRET"] = "test-flask-secret"
os.environ["ALPACA_PAPER_KEY"] = "test-alpaca-key"
os.environ["ALPACA_PAPER_SECRET"] = "test-alpaca-secret"
os.environ["OANDA_PRACTICE_KEY"] = "test-oanda-key"
os.environ["OANDA_PRACTICE_ACCOUNT_ID"] = "test-oanda-account"
# Forced empty, NOT just left unset: config.py calls load_dotenv() at
# import time, and a real local .env (gitignored, used for manually
# testing Discord alerting -- see alerts.py) may set a real, live
# webhook URL. load_dotenv() never overrides an already-set env var, so
# setting this here BEFORE config.py is ever imported guarantees tests
# never post a real message to Discord, no matter what's in .env.
os.environ["DISCORD_ALERT_WEBHOOK_URL"] = ""
# Same reasoning, same guarantee: a real local .env could have a real
# GITHUB_DISPATCH_TOKEN in it (see alerts.py/self-heal.yml) -- forcing
# this empty means tests can never fire a real repository_dispatch
# event (which would trigger a real Claude Code run and, in the worst
# case, a real PR against a real GitHub repo).
os.environ["GITHUB_DISPATCH_TOKEN"] = ""


# --- Fake Postgres: a fake cursor/connection/pool stack that db.py's
# real functions (save_trade, get_setting, get_recent_trades, ...) run
# against unmodified. Each branch below matches one of the small, fixed
# set of literal SQL statements db.py issues (normalized to collapse
# whitespace and uppercase) and reads/writes an in-memory dict instead
# of a real table. If db.py ever adds a new statement, a test hitting
# it here fails loudly (AssertionError) rather than silently returning
# nothing.
def _dispatch(store, sql_upper, params, as_dict):
    if sql_upper.startswith(("CREATE TABLE", "ALTER TABLE", "CREATE INDEX")):
        return []  # init_schema()'s DDL -- nothing to simulate

    if sql_upper.startswith("INSERT INTO TRADES"):
        action, symbol, asset_class, qty, price, pnl, regime, source, explanation, strategy_id = params
        row = {
            "executed_at": datetime.now(timezone.utc), "action": action,
            "symbol": symbol, "asset_class": asset_class, "qty": qty,
            "price": price, "pnl": pnl, "regime": regime, "source": source,
            "explanation": explanation, "strategy_id": strategy_id,
        }
        store["trades"].insert(0, row)  # newest-first, matches ORDER BY executed_at DESC
        return [(len(store["trades"]), row["executed_at"])]

    if sql_upper.startswith("SELECT EXECUTED_AT, ACTION"):
        return [dict(r) for r in store["trades"][:params[0]]]

    if sql_upper.startswith("INSERT INTO EQUITY_HISTORY"):
        (equity,) = params
        store["equity_history"].insert(0, {"recorded_at": datetime.now(timezone.utc), "equity": equity})
        return []

    if sql_upper.startswith("SELECT RECORDED_AT, EQUITY"):
        return [dict(r) for r in store["equity_history"][:params[0]]]

    if sql_upper.startswith("INSERT INTO BOT_SETTINGS"):
        key, value_json = params
        store["settings"][key] = value_json
        return []

    if sql_upper.startswith("SELECT VALUE FROM BOT_SETTINGS"):
        (key,) = params
        value_json = store["settings"].get(key)
        return [] if value_json is None else [(value_json,)]

    if sql_upper.startswith("INSERT INTO MARKET_REGIME"):
        symbol, regime, adx, volatility = params
        store["regimes"][symbol] = {
            "symbol": symbol, "recorded_at": datetime.now(timezone.utc),
            "regime": regime, "adx": adx, "volatility": volatility,
        }
        return []

    if sql_upper.startswith("SELECT SYMBOL, RECORDED_AT, REGIME, ADX, VOLATILITY"):
        (symbol,) = params
        row = store["regimes"].get(symbol)
        return [] if row is None else [dict(row)]

    if sql_upper.startswith("INSERT INTO ERROR_LOG"):
        level, source, message = params
        store["errors"].insert(0, {
            "occurred_at": datetime.now(timezone.utc), "level": level,
            "source": source, "message": message,
        })
        return []

    if sql_upper.startswith("SELECT OCCURRED_AT, LEVEL, SOURCE, MESSAGE"):
        return [dict(r) for r in store["errors"][:params[0]]]

    if sql_upper.startswith("INSERT INTO WEBHOOK_SIGNALS"):
        symbol, action, manual_flag = params
        new_id = len(store["webhook_signals"]) + 1
        store["webhook_signals"].append({
            "id": new_id, "symbol": symbol, "action": action, "manual_flag": manual_flag,
            "status": "pending", "received_at": datetime.now(timezone.utc),
            "completed_at": None, "error_message": None,
        })
        return [(new_id,)]

    if sql_upper.startswith("UPDATE WEBHOOK_SIGNALS SET STATUS = 'PROCESSING'"):
        (signal_id,) = params
        for row in store["webhook_signals"]:
            if row["id"] == signal_id:
                row["status"] = "processing"
        return []

    if sql_upper.startswith("UPDATE WEBHOOK_SIGNALS SET STATUS = 'DONE'"):
        (signal_id,) = params
        for row in store["webhook_signals"]:
            if row["id"] == signal_id:
                row["status"] = "done"
                row["completed_at"] = datetime.now(timezone.utc)
        return []

    if sql_upper.startswith("UPDATE WEBHOOK_SIGNALS SET STATUS = 'FAILED'"):
        error_message, signal_id = params
        for row in store["webhook_signals"]:
            if row["id"] == signal_id:
                row["status"] = "failed"
                row["completed_at"] = datetime.now(timezone.utc)
                row["error_message"] = error_message
        return []

    if sql_upper.startswith("SELECT ID, SYMBOL, ACTION, MANUAL_FLAG"):
        return [dict(r) for r in store["webhook_signals"] if r["status"] == "pending"]

    if sql_upper.startswith("SELECT ID, SYMBOL, ACTION, STATUS, RECEIVED_AT, ERROR_MESSAGE"):
        return [dict(r) for r in store["webhook_signals"] if r["status"] in ("processing", "failed")]

    if sql_upper.startswith("SELECT COALESCE(MAX(VERSION), 0) FROM STRATEGIES"):
        (name,) = params
        existing = [r["version"] for r in store["strategies"] if r["name"] == name]
        return [(max(existing) if existing else 0,)]

    if sql_upper.startswith("INSERT INTO STRATEGIES"):
        name, version, params_json, description, timeframe = params
        new_id = len(store["strategies"]) + 1
        row = {
            "id": new_id, "name": name, "version": version, "params": params_json,
            "description": description, "timeframe": timeframe, "created_at": datetime.now(timezone.utc),
        }
        store["strategies"].append(row)
        return [dict(row)]

    if sql_upper.startswith("SELECT ID, NAME, VERSION, PARAMS, DESCRIPTION, TIMEFRAME, CREATED_AT FROM STRATEGIES WHERE ID"):
        (strategy_id,) = params
        for row in store["strategies"]:
            if row["id"] == strategy_id:
                return [dict(row)]
        return []

    if sql_upper.startswith("SELECT ID, NAME, VERSION, PARAMS, DESCRIPTION, TIMEFRAME, CREATED_AT FROM STRATEGIES ORDER BY ID DESC"):
        return [dict(r) for r in sorted(store["strategies"], key=lambda r: r["id"], reverse=True)]

    if sql_upper.startswith("SELECT ID, NAME, VERSION, PARAMS, DESCRIPTION, TIMEFRAME, CREATED_AT FROM STRATEGIES WHERE NAME"):
        (name,) = params
        matches = [r for r in store["strategies"] if r["name"] == name]
        if not matches:
            return []
        return [dict(max(matches, key=lambda r: r["version"]))]

    if sql_upper.startswith("UPDATE STRATEGIES SET TIMEFRAME"):
        timeframe, name = params
        for row in store["strategies"]:
            if row["name"] == name and row.get("timeframe") is None:
                row["timeframe"] = timeframe
        return []

    if sql_upper.startswith("INSERT INTO SYMBOL_STRATEGY_ASSIGNMENTS"):
        symbol, strategy_id = params
        store["symbol_strategy_assignments"][symbol] = {
            "symbol": symbol, "strategy_id": strategy_id, "assigned_at": datetime.now(timezone.utc),
        }
        return []

    if sql_upper.startswith(
        "SELECT S.ID, S.NAME, S.VERSION, S.PARAMS, S.DESCRIPTION, S.TIMEFRAME, SSA.ASSIGNED_AT FROM SYMBOL_STRATEGY_ASSIGNMENTS SSA "
        "JOIN STRATEGIES S ON S.ID = SSA.STRATEGY_ID WHERE SSA.SYMBOL"
    ):
        (symbol,) = params
        assignment = store["symbol_strategy_assignments"].get(symbol)
        if assignment is None:
            return []
        strategy = next((r for r in store["strategies"] if r["id"] == assignment["strategy_id"]), None)
        if strategy is None:
            return []
        return [{
            "id": strategy["id"], "name": strategy["name"], "version": strategy["version"],
            "params": strategy["params"], "description": strategy["description"],
            "timeframe": strategy.get("timeframe"), "assigned_at": assignment["assigned_at"],
        }]

    if sql_upper.startswith("SELECT SSA.SYMBOL, S.ID, S.NAME"):
        rows = []
        for symbol, assignment in store["symbol_strategy_assignments"].items():
            strategy = next((r for r in store["strategies"] if r["id"] == assignment["strategy_id"]), None)
            if strategy is None:
                continue
            rows.append({
                "symbol": symbol, "id": strategy["id"], "name": strategy["name"],
                "version": strategy["version"], "params": strategy["params"],
                "description": strategy["description"], "timeframe": strategy.get("timeframe"),
                "assigned_at": assignment["assigned_at"],
            })
        return rows

    raise AssertionError("Fake DB got an unrecognized query: {!r}".format(sql_upper))


class _FakeCursor:
    def __init__(self, store, as_dict):
        self._store = store
        self._as_dict = as_dict
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        sql_upper = " ".join(query.split()).upper()
        self._rows = _dispatch(self._store, sql_upper, params or (), self._as_dict)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, store):
        self._store = store

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self._store, as_dict=cursor_factory is not None)

    def commit(self):
        pass

    def rollback(self):
        pass


class _FakePool:
    """Stands in for psycopg2.pool.SimpleConnectionPool -- db.get_conn()
    only ever calls .getconn()/.putconn() on whatever db._pool is, so
    that's the only surface this needs to satisfy."""

    def __init__(self, store):
        self._store = store

    def getconn(self):
        return _FakeConnection(self._store)

    def putconn(self, conn):
        pass


# Shared in-memory "database" for the whole test session. Cleared before
# each route test by reset_state() below. Exposed to tests via the
# db_store fixture for seeding/asserting on persisted data directly.
_DB_STORE = {
    "trades": [], "equity_history": [], "settings": {}, "regimes": {}, "errors": [],
    "webhook_signals": [], "strategies": [], "symbol_strategy_assignments": {},
}

# Injected before server.py (or anything importing it) ever runs --
# module-level, not inside a fixture, since a fixture only runs when a
# test requests it, which can be too late if something imports server.py
# at collection time. See db.init_pool()'s docstring for why this makes
# server.py's own init_pool()/init_schema() calls safe no-ops.
db._pool = _FakePool(_DB_STORE)


# --- Fake brokers: safe, deterministic in-memory stand-ins for every
# BrokerInterface method, assigned directly onto server.py's real
# alpaca_broker/oanda_broker instances after import (see app_module()).
# Plain functions, not bound methods -- assigning a function directly as
# an instance attribute (rather than via the class) means Python calls
# it with exactly the arguments given, no implicit `self`.
def _fake_get_account_info():
    return {"equity": 10000.0, "buying_power": 10000.0, "last_equity": 10000.0}


def _fake_get_positions():
    return []


def _fake_get_price(symbol):
    return 100.0


def _fake_place_order(symbol, side, size, order_type="market"):
    return {"id": "fake-order-id", "status": "filled"}


def _fake_get_ohlcv(symbol, timeframe="1h", limit=100):
    return []


def _fake_get_historical_bars(symbol, timeframe="1h", start=None, end=None):
    return []


def _fake_cancel_order(order_id):
    return {"status": "cancelled"}


_FAKE_BROKER_METHODS = {
    "get_account_info": _fake_get_account_info,
    "get_positions": _fake_get_positions,
    "get_price": _fake_get_price,
    "place_order": _fake_place_order,
    "get_ohlcv": _fake_get_ohlcv,
    "get_historical_bars": _fake_get_historical_bars,
    "cancel_order": _fake_cancel_order,
}

# state.py attributes route handlers mutate -- snapshotted right after
# server.py's import-time load_persisted_state() runs (against the empty
# fake DB above, so this snapshot is exactly state.py's own defaults),
# then restored before every route test so tests can't leak state into
# each other.
_STATE_ATTRS = [
    "last_signal_time", "trade_log", "equity_history", "watched_symbols",
    "bot_enabled", "max_trades_per_day", "risk_percent", "trades_today",
    "risk_caps", "current_day", "failed_login_attempts", "failed_webhook_attempts",
    "last_webhook_at", "alerted_webhook_silence", "peak_price_since_entry",
]
_STATE_SNAPSHOT = {}


@pytest.fixture(scope="session")
def app_module():
    """Imports server.py exactly once for the whole session (import has
    real side effects: constructs broker clients, starts a scheduler,
    registers Hermes, loads persisted state -- not something to redo per
    test). Replaces broker methods with safe fakes and stops the
    scheduler immediately after, before returning the module."""
    import server as server_module

    # The regime-check/safety-check jobs are scheduled to fire ~immediately
    # (next_run_time=now(), by design -- see server.py's comments) against
    # whatever real broker methods existed at that instant. Patch the fakes
    # in first, then stop the scheduler so it never fires again.
    for broker in (server_module.alpaca_broker, server_module.oanda_broker):
        for name, fn in _FAKE_BROKER_METHODS.items():
            setattr(broker, name, fn)
    server_module.scheduler.shutdown(wait=False)

    import state
    _STATE_SNAPSHOT.update({name: copy.deepcopy(getattr(state, name)) for name in _STATE_ATTRS})

    return server_module


@pytest.fixture
def reset_state(app_module):
    """Restores state.py to its post-import snapshot and clears the fake
    DB + risk manager's runtime halt state -- run before every route
    test (via client/auth_client/db_store below) so they're isolated
    from each other regardless of execution order."""
    import state
    for name, value in _STATE_SNAPSHOT.items():
        setattr(state, name, copy.deepcopy(value))

    for key in _DB_STORE:
        _DB_STORE[key].clear()

    rm = app_module.risk_manager
    rm.daily_pnl = {k: 0.0 for k in rm.asset_classes}
    rm.trading_halted = {k: False for k in rm.asset_classes}
    rm.account_halted = False
    rm.starting_equity_today = None


@pytest.fixture
def db_store(reset_state):
    """The fake DB's in-memory tables, for seeding data before a route
    call or asserting on what a route persisted."""
    return _DB_STORE


@pytest.fixture
def client(app_module, reset_state):
    return app_module.app.test_client()


@pytest.fixture
def auth_client(client):
    """A test client with an already-authenticated dashboard session --
    for routes gated by session.get('auth') without exercising /api/login
    itself in every test that needs to be logged in."""
    with client.session_transaction() as sess:
        sess["auth"] = True
    return client
