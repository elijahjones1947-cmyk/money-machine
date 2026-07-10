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
    """Call once at app startup."""
    global _pool
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
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_executed_at ON trades (executed_at DESC);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_regime_symbol_time ON market_regime (symbol, recorded_at DESC);
            """)


# --- Trades -----------------------------------------------------------

def save_trade(action, symbol, asset_class, qty, price, pnl=None, regime=None, source=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO trades (action, symbol, asset_class, qty, price, pnl, regime, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, executed_at;
                """,
                (action, symbol, asset_class, qty, price, pnl, regime, source),
            )
            return cur.fetchone()


def get_recent_trades(limit=200):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT executed_at, action, symbol, asset_class, qty, price, pnl, regime, source
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
