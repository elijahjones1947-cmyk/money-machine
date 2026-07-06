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

current_day = None  # used to detect day rollover and reset daily counters
