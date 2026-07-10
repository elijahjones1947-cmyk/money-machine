import os


def require_env(key):
    value = os.environ.get(key)
    if not value or not value.strip():
        raise RuntimeError("Missing required environment variable: {}".format(key))
    return value.strip()


def optional_env(key, default=None):
    value = os.environ.get(key)
    if value is None:
        return default
    return value.strip()


# "paper" or "live" — controls BOTH Alpaca and OANDA at once.
# Set via Railway env var. Defaults to "paper" if unset (safe default).
TRADING_MODE = optional_env("TRADING_MODE", "paper")

# App-level secrets — always required, no fallback.
WEBHOOK_SECRET = require_env("WEBHOOK_SECRET")
DASHBOARD_PASSWORD = require_env("DASHBOARD_PASSWORD")
FLASK_SECRET = require_env("FLASK_SECRET")

# Optional — Hermes (the chat agent) is fully disabled if this isn't
# set, rather than the app crashing at startup. Get one from
# console.anthropic.com and set it as a Railway env var when ready.
ANTHROPIC_API_KEY = optional_env("ANTHROPIC_API_KEY")

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
# Crypto gets its OWN (tighter) position sizing — it runs 3-5x the
# volatility of stocks/forex, so reusing their thresholds would be
# too loose. No max_leverage key for crypto: Alpaca crypto is spot-only
# (non-marginable), so leverage isn't a relevant risk lever here.
# safety_stop_loss_pct: an INDEPENDENT backstop, not the strategy's own
# intended exit -- see run_position_safety_checks() in server.py. This
# is deliberately looser than the Pine Script strategy's own stop
# (backtest/strategy.py's default stop_loss_pct is 0.35%) so it doesn't
# fight normal strategy-driven exits; it only fires if a position's
# unrealized loss blows through this threshold, which should only
# happen if something's actually wrong (a missed/failed webhook exit,
# TradingView down, etc.) rather than in the ordinary course of the
# strategy working as intended. Wider for crypto than stock/forex since
# normal crypto volatility is bigger -- a tight threshold there would
# force-close positions on routine noise, not just real problems.
RISK_CONFIG = {
    "paper": {
        "stock": {"max_position_size_pct": 0.10, "max_daily_loss_pct": 0.05, "max_open_positions": 5, "safety_stop_loss_pct": 0.02},
        "forex": {"max_position_size_pct": 0.05, "max_daily_loss_pct": 0.03, "max_open_positions": 3, "max_leverage": 20, "safety_stop_loss_pct": 0.015},
        "crypto": {"max_position_size_pct": 0.03, "max_daily_loss_pct": 0.02, "max_open_positions": 3, "safety_stop_loss_pct": 0.05},
        "account_wide": {"max_daily_loss_pct": 0.08},
    },
    "live": {
        "stock": {"max_position_size_pct": 0.05, "max_daily_loss_pct": 0.03, "max_open_positions": 5, "safety_stop_loss_pct": 0.02},
        "forex": {"max_position_size_pct": 0.02, "max_daily_loss_pct": 0.01, "max_open_positions": 3, "max_leverage": 10, "safety_stop_loss_pct": 0.015},
        "crypto": {"max_position_size_pct": 0.015, "max_daily_loss_pct": 0.01, "max_open_positions": 3, "safety_stop_loss_pct": 0.05},
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


# Official list TradingView publishes for webhook-sending IPs (verified
# against https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/
# directly, not copied from a third party -- re-check that page if this
# ever needs updating, TradingView could change it without much notice).
TRADINGVIEW_WEBHOOK_IPS = {"52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7"}

# 'off' (default): no IP check at all.
# 'log': compute whether the request's IP would pass the allowlist and
#        log a warning if not, but NEVER reject -- use this first to
#        confirm Railway is forwarding the real client IP correctly
#        (X-Forwarded-For) before trusting it, since getting this wrong
#        would otherwise silently block every real trade signal.
# 'enforce': actually reject requests from IPs outside the allowlist.
WEBHOOK_IP_MODE = optional_env("WEBHOOK_IP_MODE", "off").lower()


# Regime classifier thresholds. ADX is a normalized 0-100 measure of trend
# strength, so the standard Wilder threshold (25 = strong trend) applies
# the same way across every asset class — no need to tune it per-asset.
#
# Bollinger Band width (as a % of price), however, genuinely differs by
# asset class: forex majors typically show much narrower bands than
# crypto even in "volatile" conditions, so each asset class needs its
# own bb_width_volatile threshold. These are starting points to tune
# once you have real regime data to look back on (which is exactly what
# the backtesting phase, still ahead, will help validate).
REGIME_CONFIG = {
    "stock": {"adx_trend": 25, "bb_width_volatile": 5.0},
    "forex": {"adx_trend": 25, "bb_width_volatile": 1.5},
    "crypto": {"adx_trend": 25, "bb_width_volatile": 10.0},
}


def get_regime_config():
    return REGIME_CONFIG
