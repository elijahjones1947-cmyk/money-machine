"""
In-memory state for the bot. Same caveat as before: this resets to
these defaults whenever Railway restarts the process (no database).
Now split per asset class ('stock' / 'forex' / 'crypto') wherever it matters.
"""

last_signal_time = {}
trade_log = []  # each entry now includes an 'asset_class' field
equity_history = {"times": [], "values": []}  # combined equity, both brokers

# Scoped down to 2 symbols per asset class (from 4/4/3) -- a deliberate
# product decision to concentrate live trading rather than spread thin
# across a wide, mostly-idle watchlist. Dropped: MSFT, NVDA, SPY (stock),
# EUR_USD, GBP_USD (forex), SOL/USD (crypto). See server.py's
# _process_trade_signal docstring for how this is actually ENFORCED
# (removing a symbol from here alone does not stop /webhook from
# accepting it -- that's a separate server-side gate).
watched_symbols = {
    "stock": ["AAPL", "HOOD"],
    "forex": ["GBP_JPY", "USD_JPY"],
    "crypto": ["BTC/USD", "ETH/USD"],
}

bot_enabled = True  # manual global kill switch (overrides all asset classes)

max_trades_per_day = {"stock": 20, "forex": 20, "crypto": 10}
risk_percent = {"stock": 10, "forex": 5, "crypto": 3}
trades_today = {"stock": 0, "forex": 0, "crypto": 0}

# Populated at startup from config.get_risk_config() (a deep copy, not
# the same dict -- see server.py) and merged with any persisted
# Settings overrides in load_persisted_state(). This is the single
# source of truth the risk manager enforces AND that Settings edits --
# previously the enforced caps only ever lived in hardcoded config.py,
# completely invisible/unchangeable from the dashboard, while only the
# risk_percent sizing knob above was ever adjustable. That split (one
# adjustable number, one invisible one) is what caused a real 2-day
# forex outage when they drifted apart.
risk_caps = {}

current_day = None  # used to detect day rollover and reset daily counters

# Rolling timestamps of failed auth attempts (dashboard login secret,
# webhook secret) -- used only to log escalating warnings on repeated
# failures, NOT to auto-block further attempts. Deliberately not a
# hard lockout: a lockout that blocks the webhook path on repeated
# failures risks self-inflicted denial of service (e.g. a secret
# rotation gone slightly wrong, or TradingView retrying a stale alert
# repeatedly) blocking REAL trade signals right when they matter --
# worse than the brute-force risk it would guard against for a
# single-user bot. See server.py's _record_failed_attempt.
failed_login_attempts = []
failed_webhook_attempts = []

# Discord alerting (see alerts.py). last_webhook_at is per SYMBOL (keyed
# by the exact symbol string in a webhook payload, e.g. 'AAPL',
# 'EUR_USD', 'BTC/USD'), set on every /webhook hit carrying that symbol
# (regardless of whether the call passes auth -- see server.py's
# webhook() route) so the webhook-silence check can tell "TradingView
# stopped reaching us" apart from "TradingView's been quiet because
# there's nothing to signal on right now". Per-symbol, not per-asset-
# class: a per-class clock used to let one busy symbol (e.g. NVDA firing
# every 30m) reset the shared clock and mask a DIFFERENT symbol in the
# SAME class (e.g. AAPL) going silent at the same time -- each symbol's
# clock only moves when THAT symbol's own webhooks arrive.
# broker_error_timestamps is a rolling log of recent broker errors, same
# pattern as failed_login_attempts/failed_webhook_attempts above.
# last_broker_error_detail holds the most recent one's traceback text
# (see server.py's alerts.record_broker_error(detail=...) call sites),
# forwarded as context in the repository_dispatch payload that triggers
# the self-heal GitHub Actions workflow.
last_webhook_at = {}
broker_error_timestamps = []
last_broker_error_detail = None

# One-shot latches so the scheduled alert check (server.py's
# run_alert_checks, every 5 min) doesn't re-send the same Discord alert
# every cycle a condition stays true -- each flips back to False the
# moment its condition clears, so the NEXT occurrence alerts again. See
# alerts.py's check_and_alert_* functions. alerted_webhook_silence is
# per-symbol, same reasoning as last_webhook_at above.
alerted_account_halted = False
alerted_trading_halted = {"stock": False, "forex": False, "crypto": False}
alerted_webhook_silence = {}
alerted_broker_errors = False
