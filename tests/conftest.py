"""
Test suite scope, read this before adding more tests or wondering why
there's no server.py/Flask route coverage:

Covered: risk/risk_manager.py (the exact code that's caused two real
production incidents this build), regime.py's classifier math, backtest/
strategy.py's signal computation (shared by the backtester AND the live
signal sanity-check), and backtest/metrics.py (shared by the backtest
results AND the live-performance section). These are all pure-logic
modules with no Flask/DB/broker dependency, so they're safe and cheap
to import directly.

NOT covered: server.py's routes (webhook, /api/manual_trade, /api/dashboard,
etc.) and _process_trade_signal's full flow. server.py constructs real
broker clients and calls db.init_pool() at MODULE IMPORT TIME -- init_pool()
opens real Postgres connections immediately (psycopg2.pool.SimpleConnectionPool
eagerly opens minconn connections in its constructor), so importing server.py
at all requires a reachable DATABASE_URL. Getting proper route-level test
coverage would need refactoring server.py to defer broker/DB construction
(dependency injection) rather than doing it all as import-time side effects --
a real improvement worth doing, but a bigger, riskier change than "add tests"
should be bundled with. Flagging this gap rather than pretending the suite
is more complete than it is.
"""
