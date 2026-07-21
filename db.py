"""
Postgres persistence layer.

Why this exists: everything in state.py lives in memory and resets to
defaults every time Railway restarts the process (deploys, crashes,
routine restarts — all of it). That's been a known limitation since
the very first version of this bot. This module fixes it for the
things that actually matter to keep across restarts:

  - trade history (so your dashboard's stats/win-rate survive restarts)
  - equity curve history
  - your configured settings (risk %, max trades/day, bot on/off, watchlists)
  - market_regime (schema ready now for the regime classifier we'll
    build in the next phase — not populated yet, but the table exists
    so that step doesn't need its own migration later)

Uses a small connection pool (not one connection per request, not a
brand-new connection per request) since Flask can serve the webhook
and dashboard concurrently.
"""

import os
import json
import logging
from contextlib import contextmanager

import psycopg2
import psycopg2.pool
import psycopg2.extras


_pool = None


def init_pool():
    """Call once at app startup.

    Idempotent: if _pool is already set -- a real pool from an earlier
    call, OR one injected directly onto db._pool before this ever runs
    -- this is a no-op rather than reconnecting/overwriting it. That's
    what lets tests set db._pool to a fake (mock cursor/connection, no
    real Postgres involved) before server.py is imported: server.py's
    own module-level db.init_pool() call then just sees a pool already
    present and returns immediately, so importing/exercising the app in
    tests never needs a reachable DATABASE_URL. See tests/conftest.py.
    """
    global _pool
    if _pool is not None:
        return
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "Missing required environment variable: DATABASE_URL "
            "(add a Postgres service on Railway and link its DATABASE_URL "
            "to this service)"
        )
    _pool = psycopg2.pool.SimpleConnectionPool(1, 10, database_url)


@contextmanager
def get_conn():
    if _pool is None:
        raise RuntimeError("Database pool not initialized — call init_pool() at startup")
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def init_schema():
    """Create tables if they don't exist yet. Safe to call every startup."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id SERIAL PRIMARY KEY,
                    executed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    action TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    qty NUMERIC NOT NULL,
                    price NUMERIC NOT NULL,
                    pnl NUMERIC
                );
            """)
            # Migration-safe: this table already exists in production
            # without a regime column, so ADD COLUMN IF NOT EXISTS rather
            # than assuming a fresh CREATE TABLE covers it.
            cur.execute("""
                ALTER TABLE trades ADD COLUMN IF NOT EXISTS regime TEXT;
            """)
            cur.execute("""
                ALTER TABLE trades ADD COLUMN IF NOT EXISTS source TEXT;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS equity_history (
                    id SERIAL PRIMARY KEY,
                    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    equity NUMERIC NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS market_regime (
                    id SERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    regime TEXT NOT NULL,
                    adx NUMERIC,
                    volatility NUMERIC
                );
            """)
            # Written by server.py's DBLogHandler (every WARNING+ log
            # record app-wide) and read by discord_bot.py -- a separate
            # process with no direct access to this process's in-memory
            # logs, so Postgres is the handoff point. See both files.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS error_log (
                    id SERIAL PRIMARY KEY,
                    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    level TEXT NOT NULL,
                    source TEXT NOT NULL,
                    message TEXT NOT NULL
                );
            """)
            # The durability layer behind webhook_queue.py: /webhook
            # writes a row here SYNCHRONOUSLY, before returning 202 --
            # that write (not the in-memory queue) is what makes an
            # accepted signal survive a process kill/restart between
            # "accepted" and "executed". status starts 'pending', moves
            # to 'processing' right before _process_trade_signal runs,
            # then 'done' or 'failed' after. See server.py's
            # recover_pending_webhook_signals() (run at startup) for how
            # 'pending' rows left over from a crash get resumed, and why
            # 'processing'/'failed' rows deliberately do NOT get
            # auto-resumed (can't safely tell whether the broker call
            # already fired before the crash/error -- retrying blind
            # risks a DUPLICATE order, which is worse than one signal
            # needing a human to look at it).
            cur.execute("""
                CREATE TABLE IF NOT EXISTS webhook_signals (
                    id SERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    manual_flag BOOLEAN NOT NULL DEFAULT FALSE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    completed_at TIMESTAMPTZ,
                    error_message TEXT
                );
            """)
            # Explanation text (Phase 1) and which strategy a trade was
            # entered/exited under (Phase 4) -- both migration-safe ADD
            # COLUMNs since `trades` already has real production rows
            # without either. strategy_id has no FK constraint on purpose:
            # a trade must never fail to log just because the strategies
            # table has a hiccup or the row referenced it was later
            # pruned -- same "never let a side write risk looking like the
            # trade itself failed" rule as regime/source before it.
            cur.execute("""
                ALTER TABLE trades ADD COLUMN IF NOT EXISTS explanation TEXT;
            """)
            cur.execute("""
                ALTER TABLE trades ADD COLUMN IF NOT EXISTS strategy_id INTEGER;
            """)
            # Named, versioned strategy definitions -- see
            # server.py's strategy management routes (Phase 4) and
            # strategy_knowledge.py for what params/rules mean. Rows are
            # immutable once created: "editing" a strategy's params
            # creates a NEW row with the same `name` and version+1,
            # rather than mutating one in place, so any trade's
            # strategy_id always points at the EXACT params that were
            # live when it executed -- the versioning point 5(a) asked
            # for. `name` is deliberately not UNIQUE/PRIMARY KEY: multiple
            # versions share a name on purpose.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS strategies (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    params TEXT NOT NULL,
                    description TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
            # Added after the table already had live rows -- same
            # ALTER-not-recreate pattern as trades' regime/source/
            # explanation/strategy_id columns above. Nullable: existing
            # rows need a one-time backfill (see server.py's
            # backfill_strategy_timeframes()) rather than a value this
            # migration could safely invent on its own. A first-class
            # column, not a key inside `params` -- this is a hard fact
            # about the strategy (what TradingView alert interval it
            # actually runs on), not a tunable Pine-script parameter.
            cur.execute("""
                ALTER TABLE strategies ADD COLUMN IF NOT EXISTS timeframe TEXT;
            """)
            # Which strategy (an exact, specific version -- strategy_id,
            # not just a name) is currently active for each symbol. This
            # is BOTH the per-symbol params store (Phase 0/1/3 read this
            # to know which params apply to a symbol) AND, once /webhook
            # starts requiring a strategy_id in the payload (Phase 4),
            # the source of truth /webhook validates an incoming signal's
            # strategy_id against -- a mismatch means a stale TradingView
            # alert is still firing under a strategy that's no longer
            # active for that symbol, and gets rejected rather than
            # silently executed under out-of-date logic.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS symbol_strategy_assignments (
                    symbol TEXT PRIMARY KEY,
                    strategy_id INTEGER NOT NULL,
                    assigned_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_executed_at ON trades (executed_at DESC);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_regime_symbol_time ON market_regime (symbol, recorded_at DESC);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_error_log_occurred_at ON error_log (occurred_at DESC);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_webhook_signals_status ON webhook_signals (status);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_strategies_name_version ON strategies (name, version DESC);
            """)


# --- Trades -----------------------------------------------------------

def save_trade(action, symbol, asset_class, qty, price, pnl=None, regime=None, source=None,
                explanation=None, strategy_id=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO trades (action, symbol, asset_class, qty, price, pnl, regime, source, explanation, strategy_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, executed_at;
                """,
                (action, symbol, asset_class, qty, price, pnl, regime, source, explanation, strategy_id),
            )
            return cur.fetchone()


def get_recent_trades(limit=200):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT executed_at, action, symbol, asset_class, qty, price, pnl, regime, source, explanation, strategy_id
                FROM trades
                ORDER BY executed_at DESC
                LIMIT %s;
                """,
                (limit,),
            )
            return cur.fetchall()


# --- Equity history -----------------------------------------------------

def save_equity_point(equity):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO equity_history (equity) VALUES (%s);",
                (equity,),
            )


def get_equity_history(limit=100):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT recorded_at, equity FROM equity_history
                ORDER BY recorded_at DESC
                LIMIT %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()
            return list(reversed(rows))  # oldest first, for charting


# --- Settings (bot_enabled, risk_percent, max_trades_per_day, watchlists) --

def save_setting(key, value):
    """value can be any JSON-serializable object."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bot_settings (key, value, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();
                """,
                (key, json.dumps(value)),
            )


def get_setting(key, default=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM bot_settings WHERE key = %s;", (key,))
            row = cur.fetchone()
            if row is None:
                return default
            return json.loads(row[0])


# --- Market regime (schema ready now; populated by the classifier we'll
# build in the next phase) ------------------------------------------------

def save_regime(symbol, regime, adx=None, volatility=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO market_regime (symbol, regime, adx, volatility)
                VALUES (%s, %s, %s, %s);
                """,
                (symbol, regime, adx, volatility),
            )


def get_latest_regime(symbol):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT symbol, recorded_at, regime, adx, volatility
                FROM market_regime
                WHERE symbol = %s
                ORDER BY recorded_at DESC
                LIMIT 1;
                """,
                (symbol,),
            )
            return cur.fetchone()


# --- Error log (server.py's DBLogHandler writes; discord_bot.py reads) ----

def save_error_log(level, source, message):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO error_log (level, source, message) VALUES (%s, %s, %s);",
                (level, source, message),
            )


def get_recent_errors(limit=50):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT occurred_at, level, source, message
                FROM error_log
                ORDER BY occurred_at DESC
                LIMIT %s;
                """,
                (limit,),
            )
            return cur.fetchall()


# --- Webhook signal durability (see init_schema's webhook_signals comment
# and server.py's webhook()/webhook_queue.py for the full picture) --------

def enqueue_webhook_signal(symbol, action, manual_flag):
    """The actual durability guarantee: called SYNCHRONOUSLY from
    /webhook before it returns 202, so a signal is durably recorded in
    Postgres before TradingView is ever told it was accepted. Must stay
    fast -- a single INSERT, same cost class as save_trade -- or this
    reintroduces the exact latency problem webhook_queue.py exists to
    avoid."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO webhook_signals (symbol, action, manual_flag)
                VALUES (%s, %s, %s)
                RETURNING id;
                """,
                (symbol, action, manual_flag),
            )
            return cur.fetchone()[0]


def mark_webhook_signal_processing(signal_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE webhook_signals SET status = 'processing' WHERE id = %s;",
                (signal_id,),
            )


def mark_webhook_signal_done(signal_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE webhook_signals SET status = 'done', completed_at = now() WHERE id = %s;",
                (signal_id,),
            )


def mark_webhook_signal_failed(signal_id, error_message):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE webhook_signals
                SET status = 'failed', completed_at = now(), error_message = %s
                WHERE id = %s;
                """,
                (error_message[:2000], signal_id),
            )


def get_pending_webhook_signals():
    """Rows never even started processing before the last shutdown/crash
    -- the ONLY status it's safe to auto-resume (see
    server.py's recover_pending_webhook_signals()). Ordered by id (global
    insertion order), which is what lets the caller re-enqueue them
    per-symbol and have webhook_queue.py's own FIFO guarantee reproduce
    the original arrival order within each symbol."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, symbol, action, manual_flag
                FROM webhook_signals
                WHERE status = 'pending'
                ORDER BY id;
                """
            )
            return cur.fetchall()


def get_stuck_webhook_signals():
    """Rows left in 'processing' (crash mid-execution) or 'failed' (an
    unexpected exception) -- ambiguous whether the broker call already
    fired, so these are surfaced for manual review rather than ever
    auto-resumed. See server.py's recover_pending_webhook_signals()."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, symbol, action, status, received_at, error_message
                FROM webhook_signals
                WHERE status IN ('processing', 'failed')
                ORDER BY id;
                """
            )
            return cur.fetchall()


# --- Strategy definitions + per-symbol assignment (Phase 0/4) ------------
# See init_schema()'s comments on `strategies`/`symbol_strategy_assignments`
# for the versioning/immutability model.

def create_strategy(name, params, description=None, timeframe=None):
    """Inserts a NEW, immutable strategy row -- never updates an existing
    one. `version` auto-increments per `name` (1 for a brand-new name,
    otherwise one more than the highest existing version for it), so
    "editing" a strategy in the UI/API always means calling this again
    with the same name and a modified `params`, not mutating history.
    `params` is a plain dict (same shape as backtest.strategy.
    DEFAULT_PARAMS) -- serialized the same way bot_settings already
    stores JSON values (json.dumps/loads), not a native JSONB column,
    to match this file's existing convention.

    `timeframe` (e.g. "30m", "1h") is deliberately its own keyword-only
    param, added AFTER `description` rather than inserted before it --
    server.py's /api/strategies POST route already calls this
    positionally as create_strategy(name, params, description); adding
    a new positional param in the middle would have silently shifted
    that call's description into the wrong slot. Optional/nullable
    (not required here) so existing callers -- including the dashboard's
    current "+ New strategy" form, which doesn't collect a timeframe
    yet -- keep working unchanged."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(version), 0) FROM strategies WHERE name = %s;", (name,))
            next_version = cur.fetchone()[0] + 1
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO strategies (name, version, params, description, timeframe)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, name, version, created_at;
                """,
                (name, next_version, json.dumps(params), description, timeframe),
            )
            return dict(cur.fetchone())


def get_strategy(strategy_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, name, version, params, description, timeframe, created_at FROM strategies WHERE id = %s;",
                (strategy_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            row = dict(row)
            row["params"] = json.loads(row["params"])
            return row


def list_strategies():
    """All strategy versions ever created, newest first -- the full
    history, not just the latest version per name (use
    get_latest_strategy_version for that)."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, name, version, params, description, timeframe, created_at FROM strategies ORDER BY id DESC;"
            )
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["params"] = json.loads(r["params"])
            return rows


def get_latest_strategy_version(name):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, name, version, params, description, timeframe, created_at
                FROM strategies WHERE name = %s
                ORDER BY version DESC LIMIT 1;
                """,
                (name,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            row = dict(row)
            row["params"] = json.loads(row["params"])
            return row


def assign_strategy_to_symbol(symbol, strategy_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO symbol_strategy_assignments (symbol, strategy_id, assigned_at)
                VALUES (%s, %s, now())
                ON CONFLICT (symbol) DO UPDATE SET strategy_id = EXCLUDED.strategy_id, assigned_at = now();
                """,
                (symbol, strategy_id),
            )


def get_symbol_strategy_assignment(symbol):
    """Returns the FULL joined strategy row (not just the strategy_id) --
    every caller of this (webhook validation, explanation generation)
    needs the actual params/name, not just the id, so join it here once
    rather than making every caller do a second lookup."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT s.id, s.name, s.version, s.params, s.description, s.timeframe, ssa.assigned_at
                FROM symbol_strategy_assignments ssa
                JOIN strategies s ON s.id = ssa.strategy_id
                WHERE ssa.symbol = %s;
                """,
                (symbol,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            row = dict(row)
            row["params"] = json.loads(row["params"])
            return row


def get_all_symbol_strategy_assignments():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT ssa.symbol, s.id, s.name, s.version, s.params, s.description, s.timeframe, ssa.assigned_at
                FROM symbol_strategy_assignments ssa
                JOIN strategies s ON s.id = ssa.strategy_id;
                """
            )
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["params"] = json.loads(r["params"])
            return rows


def backfill_timeframe_for_strategy_name(name, timeframe):
    """One-time migration helper (see server.py's
    backfill_strategy_timeframes()): sets `timeframe` for every version
    of `name` that doesn't already have one. WHERE timeframe IS NULL
    means this never overwrites a value that's already been set --
    whether by a previous run of this same backfill, or by
    create_strategy() specifying it directly for a strategy created
    after the column existed."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE strategies SET timeframe = %s WHERE name = %s AND timeframe IS NULL;",
                (timeframe, name),
            )
