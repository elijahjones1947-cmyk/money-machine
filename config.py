import os


def require_env(key):
    value = os.environ.get(key)
    if not value:
        raise RuntimeError("Missing required environment variable: {}".format(key))
    return value


def optional_env(key, default=None):
    return os.environ.get(key, default)


# "paper" or "live" — controls BOTH Alpaca and OANDA at once.
# Set via Railway env var. Defaults to "paper" if unset (safe default).
TRADING_MODE = optional_env("TRADING_MODE", "paper")

# App-level secrets — always required, no fallback.
WEBHOOK_SECRET = require_env("WEBHOOK_SECRET")
DASHBOARD_PASSWORD = require_env("DASHBOARD_PASSWORD")
FLASK_SECRET = require_env("FLASK_SECRET")

BROKER_CONFIG = {
    "alpaca": {
        "paper": {
            "base_url": "https://paper-api.alpaca.markets",
            "api_key": optional_env("ALPACA_PAPER_KEY"),
            "api_secret": optional_env("ALPACA_PAPER_SECRET"),
        },
        "live": {
            "base_url": "https://api.alpaca.markets",
            "api_key": optional_env("ALPACA_LIVE_KEY"),
            "api_secret": optional_env("ALPACA_LIVE_SECRET"),
        },
    },
    "oanda": {
        "paper": {
            "base_url": "https://api-fxpractice.oanda.com",
            "api_key": optional_env("OANDA_PRACTICE_KEY"),
            "account_id": optional_env("OANDA_PRACTICE_ACCOUNT_ID"),
        },
        "live": {
            "base_url": "https://api-fxtrade.oanda.com",
            "api_key": optional_env("OANDA_LIVE_KEY"),
            "account_id": optional_env("OANDA_LIVE_ACCOUNT_ID"),
        },
    },
}

# Per-mode, per-asset-class risk limits.
# Tighter on live than paper, per what we discussed.
RISK_CONFIG = {
    "paper": {
        "stock": {"max_position_size_pct": 0.10, "max_daily_loss_pct": 0.05, "max_open_positions": 5},
        "forex": {"max_position_size_pct": 0.05, "max_daily_loss_pct": 0.03, "max_open_positions": 3, "max_leverage": 20},
        "account_wide": {"max_daily_loss_pct": 0.08},
    },
    "live": {
        "stock": {"max_position_size_pct": 0.05, "max_daily_loss_pct": 0.03, "max_open_positions": 5},
        "forex": {"max_position_size_pct": 0.02, "max_daily_loss_pct": 0.01, "max_open_positions": 3, "max_leverage": 10},
        "account_wide": {"max_daily_loss_pct": 0.05},
    },
}


def get_broker_credentials(broker_name):
    """broker_name = 'alpaca' or 'oanda'"""
    creds = BROKER_CONFIG[broker_name][TRADING_MODE]
    missing = [k for k, v in creds.items() if v is None and k != "base_url"]
    if missing:
        raise RuntimeError(
            "Missing {} credentials for {} mode: {}".format(broker_name, TRADING_MODE, missing)
        )
    return creds


def get_risk_config():
    return RISK_CONFIG[TRADING_MODE]
