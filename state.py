"""
In-memory state for the bot. Same caveat as before: this resets to
these defaults whenever Railway restarts the process (no database).
Now split per asset class ('stock' / 'forex' / 'crypto') wherever it matters.
"""

last_signal_time = {}
trade_log = []  # each entry now includes an 'asset_class' field
equity_history = {"times": [], "values": []}  # combined equity, both brokers

watched_symbols = {
    "stock": ["AAPL"],
    "forex": ["EUR_USD"],
    "crypto": ["BTC/USD"],
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
