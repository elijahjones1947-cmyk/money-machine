# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An automated trading bot that trades stocks/crypto (via Alpaca) and forex (via OANDA) based on TradingView webhook
alerts. A Flask backend executes trades, enforces risk limits, and persists state to Postgres; a React SPA dashboard
(built with Vite, served by the same Flask process from `frontend/dist`) provides monitoring/control, including
Hermes, a Claude-powered chat agent with read access to bot state and a confirm-gated set of control tools.

The strategy itself ("Higher High Breakout") lives as a TradingView Pine Script and fires webhook alerts; this repo
does not contain the Pine Script, but `backtest/strategy.py` is a Python port of it used for backtesting and as a
live sanity check against incoming webhook signals.

## Commands

Backend (run from repo root):
```
pip install -r requirements.txt -r requirements-dev.txt   # install incl. test-only deps
pytest                                                      # run the full test suite
pytest tests/test_risk_manager.py                           # run a single test file
pytest tests/test_risk_manager.py::test_name -v              # run a single test
python -m backtest.runner                                    # backtest the 3 default instruments (AAPL, EUR_USD, BTC/USD)
python -m backtest.runner --symbol AAPL --asset-class stock --months 6   # backtest one instrument
python server.py                                             # run the Flask app locally (needs env vars + DATABASE_URL)
```
`requirements-dev.txt` (pytest) is NOT installed in production — Railway's build only installs `requirements.txt`.

Frontend (run from `frontend/`):
```
npm install
npm run dev        # Vite dev server with HMR
npm run build      # production build -> frontend/dist (served by Flask)
npm run lint        # oxlint
```

Production runs via `gunicorn server:app --workers 1 --threads 8` (see `Procfile`) — **must stay at `--workers 1`**;
Hermes's conversation state and `state.py`'s in-memory state are process-local, not shared across workers.

## Required environment variables

`config.py`'s `require_env` raises at import time if any of these are missing: `WEBHOOK_SECRET`,
`DASHBOARD_PASSWORD`, `FLASK_SECRET`, `DATABASE_URL` (checked in `db.init_pool()`). Broker credentials
(`ALPACA_PAPER_KEY`/`ALPACA_PAPER_SECRET`/`ALPACA_LIVE_KEY`/`ALPACA_LIVE_SECRET`,
`OANDA_PRACTICE_KEY`/`OANDA_PRACTICE_ACCOUNT_ID`/`OANDA_LIVE_KEY`/`OANDA_LIVE_ACCOUNT_ID`) are required only for
whichever mode `TRADING_MODE` selects ("paper" or "live", default "paper"). `ANTHROPIC_API_KEY` is optional — Hermes
routes return 503 without it rather than the app failing to boot.

## Architecture

**Everything currently runs as a single Flask process (`server.py`)** — brokers, risk manager, and DB pool are all
constructed at module import time as global singletons, not dependency-injected. This is why `tests/` cannot import
`server.py` at all (see below) and why the process must stay at exactly one gunicorn worker.

**One risk-checking pipeline, three entry points.** `/webhook` (TradingView alerts, shared-secret gated),
`/api/manual_trade` (dashboard buttons, session gated), and `run_position_safety_checks()` (a scheduled background
job) all funnel through the same `_process_trade_signal()` in `server.py`, so sizing, risk checks, execution, and
trade logging behave identically regardless of the source. The `source` field (`webhook`/`manual`/`safety_stop_loss`)
on each logged trade records which path fired it.

**Broker abstraction.** `brokers/base.py` defines `BrokerInterface`; `AlpacaBroker` (stock + crypto — Alpaca is
spot-only/non-marginable for crypto) and `OandaBroker` (forex) implement it and translate broker-native exceptions
into the shared error types in `errors.py` (`InsufficientFundsError`, `MarketClosedError`, `InvalidSymbolError`,
`BrokerConnectionError`). Strategy/risk/route code only ever talks to the interface. `asset_class_for_symbol()` in
`server.py` dispatches by symbol format: `/` = crypto (`BTC/USD`), `_` = forex (`EUR_USD`), else stock. `BROKERS =
{"stock": alpaca, "forex": oanda, "crypto": alpaca}` — stock and crypto share one Alpaca account, so equity/position
aggregation code (`get_combined_equity`, `get_all_positions`) must dedupe by broker identity, not just sum blindly.

**Risk management is layered, and each layer has a specific purpose — don't collapse them:**
1. `risk/risk_manager.py`'s `RiskManager` — per-asset-class daily loss halts, position size cap, max open positions,
   leverage cap (forex only), plus one account-wide daily loss breaker across combined equity. A trade that
   `reduces_position=True` (closes/shrinks existing exposure) bypasses every check here by design — a halt must never
   trap you in a position you're trying to exit.
2. `state.risk_caps` — the *live, editable* copy of `config.RISK_CONFIG`, deep-copied at startup then merged with any
   persisted Settings overrides from Postgres. `RiskManager` holds this exact dict object (not a copy), so a Settings
   API change mutates it in place and is immediately what gets enforced — this one-dict-not-two-numbers design is a
   deliberate fix for a past incident (a 2-day forex outage) where an adjustable sizing knob and an invisible
   hardcoded cap drifted apart.
3. `run_position_safety_checks()` (scheduled every 5 min) — an independent backstop, NOT the strategy's own stop
   loss. Force-closes any position whose unrealized loss breaches `safety_stop_loss_pct`, via the same
   `_process_trade_signal` path, regardless of whether TradingView ever sends a matching exit webhook. Deliberately
   looser than the Pine Script's own intended stop so it doesn't fight normal strategy exits.

**Market regime classification** (`regime.py`) tags each watched symbol as `trending` / `volatile` / `choppy` using
ADX (trend strength, same threshold across asset classes) + Bollinger Band width (asset-specific thresholds — crypto
is naturally wider than forex). Runs on a 15-minute scheduled job (`run_regime_checks` in `server.py`), independently
of trading — a classification failure only degrades logging/tagging, never blocks a trade. Regime tags get attached
to trades for later performance breakdown (`backtest/regime_tagging.py` does the same for backtested trades).

**Persistence** (`db.py`) is a thin Postgres layer (connection-pooled) backing what would otherwise be
`state.py`'s pure in-memory state, which resets on every Railway restart. `server.py`'s `load_persisted_state()`
pulls settings/trades/equity history into the `state.py` cache once at startup; every subsequent mutation (a trade,
a settings change) writes through to Postgres immediately. A DB hiccup on any individual write is logged and
swallowed — never allowed to make an already-executed broker trade look like it failed.

**Signal sanity checking**: `_sanity_check_signal()` in `server.py` independently recomputes the strategy's signal
for a symbol using `backtest/strategy.py`'s `compute_signals()` (the same Python port of the Pine Script used for
backtesting) and logs a warning — never blocks — if it disagrees with what an incoming webhook claims. Treat a
logged disagreement as "worth a look," not "the bot is broken" (timing/bar-close differences make some disagreement
expected even when both sides are individually correct).

**Hermes** (`hermes.py` + `hermes_tools.py`) is a Claude-powered chat agent scoped under `/api/hermes/*`. Read tools
(portfolio, positions, trades, regime, risk, config, market data) execute immediately when the model calls them.
Executor tools (pause/resume trading, adjust a risk limit) never auto-execute: the first executor `tool_use` in any
model response is staged as `_pending_action` and the turn stops there — the frontend must call `/api/hermes/confirm`
to actually run it. Conversation state is a module-level global, in-process only — this is the other reason the app
must stay single-worker.

**Backtesting** (`backtest/`) reuses live production code end-to-end rather than a separate simulation stack:
`strategy.py` (signal logic), `regime_tagging.py`, and `metrics.py` (win rate / max drawdown / Sharpe) are all
shared with the live `_compute_live_performance()` path in `server.py`, so the dashboard's Backtest page can show
predicted-vs-actual in directly comparable terms. `backtest/runner.py` is a CLI (`python -m backtest.runner`) that
pulls real historical bars via the broker's `get_historical_bars()`, simulates, and writes `backtest_results.json`
(read by `GET /api/backtest`, gitignored, regenerate locally when needed).

**Frontend** (`frontend/`, React 19 + Vite + react-router + react-grid-layout) is a single dashboard SPA. Widgets
(`src/widgets/`) are registered in `src/widgets/registry.js` and rendered in a user-configurable, draggable/resizable
grid (`DashboardGrid.jsx`); layout is persisted server-side via `GET/POST /ui/layout` so it survives across devices
and redeploys. `src/api.js` is the sole fetch wrapper — cookie-session auth (`credentials: 'include'`), same-origin
so no CORS/token handling needed. Flask serves the built SPA itself: `server.py`'s catch-all route
(`static_folder=None` on the Flask app, deliberately — see the comment at the top of `server.py` for why) serves a
real file from `frontend/dist` if one matches the path, otherwise falls back to `index.html` for client-side routing.

**Auth model**: dashboard login is a single shared password (`DASHBOARD_PASSWORD`) checked with
`hmac.compare_digest` (timing-safe) against a Flask session cookie — there are no per-user accounts. The
`/webhook` route is authenticated by a separate shared secret (`WEBHOOK_SECRET`) in the JSON body, since TradingView
alerts send a fixed static body with no way to compute a per-request signature. Failed attempts on both are logged
with escalating severity but deliberately never trigger a lockout (see `state.py`'s docstring on
`failed_login_attempts`/`failed_webhook_attempts`) — a lockout on the webhook path risks a self-inflicted denial of
service against real trade signals, which is worse than the brute-force risk it would guard against for a
single-user bot. An optional `WEBHOOK_IP_MODE` (`off`/`log`/`enforce`) can additionally allowlist TradingView's
published IPs; use `log` mode first to confirm Railway is forwarding real client IPs before ever switching to
`enforce`.

## Test suite scope

Read `tests/conftest.py`'s module docstring before adding tests. Covered: `risk/risk_manager.py`, `regime.py`,
`backtest/strategy.py`, `backtest/metrics.py` — pure-logic modules with no Flask/DB/broker dependency. **Not
covered**: `server.py`'s routes and `_process_trade_signal`. Importing `server.py` constructs real broker clients
and calls `db.init_pool()` at *module import time*, which eagerly opens real Postgres connections — so any test that
imports `server.py` needs a live `DATABASE_URL`. Getting real route-level coverage would require refactoring
`server.py` to defer broker/DB construction (dependency injection) instead of doing it as import-time side effects.
