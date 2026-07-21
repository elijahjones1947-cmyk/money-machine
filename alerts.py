"""
Alerting for critical bot events: the account (or one asset class)
getting halted by the risk manager, /webhook going quiet for too long
during market hours, and broker errors piling up.

Two independent channels, both best-effort and individually optional:

  - Discord (config.DISCORD_ALERT_WEBHOOK_URL): a human-readable embed,
    same "optional feature, no crash" pattern config.py already uses for
    ANTHROPIC_API_KEY/Hermes.
  - GitHub repository_dispatch (config.GITHUB_DISPATCH_TOKEN): fires
    .github/workflows/self-heal.yml, which runs Claude Code to diagnose
    the alert and -- only if it finds a genuine code bug, not the risk
    system correctly doing its job -- draft a PR. See that workflow file
    for what happens on the other end; this module's only job is to
    hand it enough context (alert type, scope, timestamp, traceback if
    any) to work with.

Neither channel ever raises: a Discord/GitHub outage or a missing
token should never be able to affect trading, matching every other
best-effort side-channel in this codebase (DB persistence, regime
tagging, ...).

Each check is edge-triggered via a one-shot latch in state.py
(alerted_account_halted, alerted_trading_halted, etc.): it alerts once
when a condition first becomes true, then stays quiet on every
subsequent scheduled check while the condition persists, and resets the
moment the condition clears so the NEXT occurrence alerts again. Both
channels fire together, from the same latch transition -- there's no
world where GitHub gets a dispatch that Discord doesn't also hear about.
"""

import logging
import time
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    _NY_TZ = ZoneInfo("America/New_York")
except Exception:
    # Shouldn't happen on the target runtime (Python 3.12 + the tzdata
    # package), but if it ever does, fail open rather than silently
    # never alerting on webhook silence.
    _NY_TZ = None

import requests

import config
import state

_DISCORD_TIMEOUT_SECONDS = 5
_GITHUB_TIMEOUT_SECONDS = 10

# Not a secret -- this repo is already public, and knowing its name
# doesn't grant any access. GITHUB_DISPATCH_TOKEN (config.py) is what's
# actually sensitive.
GITHUB_REPO = "elijahjones1947-cmyk/money-machine"
GITHUB_DISPATCH_URL = "https://api.github.com/repos/{}/dispatches".format(GITHUB_REPO)

WEBHOOK_SILENCE_THRESHOLD_SECONDS = 2 * 60 * 60  # 2 hours

# A real incident (2026-07-12, see git log around this comment) showed why
# these two numbers can't just be "reasonable-sounding round numbers" in
# isolation: they were 15 min / 5, matching server.py's run_regime_checks
# interval (also 15 min) exactly. run_regime_checks can contribute at most
# one broker error per watched symbol per run, so a single symbol stuck
# failing every cycle (which is exactly what happened -- persistent OANDA
# timeouts on EUR_USD) could never accumulate more than ~1-2 errors within
# any 15-minute window, no matter how many hours it kept failing. The
# threshold was structurally unreachable for exactly the failure pattern
# most likely to occur in practice.
#
# 45 minutes / 3 fixes that: three consecutive 15-minute regime-check
# failures for even just ONE symbol land inside a single 45-minute window
# and trip the alert, while one or two isolated blips (increasingly rare
# now that brokers/oanda_broker.py retries connection/read timeouts once
# before giving up -- see its docstring) still don't. Any other error
# source (run_position_safety_checks every 5 min, live webhook/dashboard
# traffic) only makes a real outage cross this threshold faster.
BROKER_ERROR_WINDOW_SECONDS = 45 * 60  # 45 minutes
BROKER_ERROR_THRESHOLD = 3  # >= this many broker errors within the window above triggers an alert


def _post_to_discord(title, description, color=0xE74C3C):
    """color defaults to red -- every ALERT in this module is a
    "something's wrong" condition. post_trade_notification below passes
    its own (blue) so a routine trade doesn't visually read as an
    incident in the same channel."""
    webhook_url = config.DISCORD_ALERT_WEBHOOK_URL
    if not webhook_url:
        return
    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=_DISCORD_TIMEOUT_SECONDS)
        if resp.status_code >= 300:
            logging.warning("Discord alert post failed ({}): {}".format(resp.status_code, resp.text[:200]))
    except requests.RequestException as e:
        logging.warning("Discord alert post failed: {}".format(e))


_TRADE_NOTIFICATION_COLOR = 0x3498DB  # blue -- routine trade info, deliberately distinct from the red "something's wrong" alerts above


def post_trade_notification(action, symbol, asset_class, qty, price, pnl, explanation):
    """Pushes a trade's generated explanation (trade_explanations.py) to
    Discord as it happens -- proactive instead of requiring someone to
    open the dashboard's Trade Log page or ask Hermes after the fact.
    Reuses the exact same webhook/best-effort pattern as every alert
    above via _post_to_discord (silently no-ops if
    DISCORD_ALERT_WEBHOOK_URL isn't set, never raises).

    NOT edge-triggered/latched like the alerts above -- that machinery
    exists so a PERSISTING condition (halted, silent, erroring) doesn't
    spam every scheduled check while it continues being true. A trade is
    a one-time, discrete event, not a persisting condition -- there's
    nothing to latch against, so this posts once per call, always."""
    summary = "{} {} {} @ {}".format(action.upper(), qty, symbol, price)
    if pnl is not None:
        summary += " (P&L: {:+.2f})".format(pnl)
    description = summary
    if explanation:
        description += "\n\n" + explanation
    _post_to_discord("{} {}".format(action.upper(), symbol), description, color=_TRADE_NOTIFICATION_COLOR)


def _trigger_github_dispatch(event_type, client_payload):
    """POSTs a repository_dispatch event that fires
    .github/workflows/self-heal.yml. event_type must match one of that
    workflow's `on.repository_dispatch.types` entries exactly, or
    GitHub silently accepts the event and no workflow runs at all."""
    token = config.GITHUB_DISPATCH_TOKEN
    if not token:
        return
    headers = {
        "Authorization": "Bearer {}".format(token),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body = {"event_type": event_type, "client_payload": client_payload}
    try:
        resp = requests.post(GITHUB_DISPATCH_URL, json=body, headers=headers, timeout=_GITHUB_TIMEOUT_SECONDS)
        if resp.status_code >= 300:
            logging.warning("GitHub dispatch failed ({}): {}".format(resp.status_code, resp.text[:200]))
    except requests.RequestException as e:
        logging.warning("GitHub dispatch failed: {}".format(e))


def record_broker_error(detail=None):
    """Call from every place that catches a broker error and swallows
    it (get_all_positions, get_combined_equity, the scheduled regime/
    safety-check jobs, _process_trade_signal's own handler, ...) --
    appends now() and prunes anything outside BROKER_ERROR_WINDOW_SECONDS,
    mirroring server.py's _record_failed_attempt pattern for auth
    failures.

    `detail` (typically traceback.format_exc(), called from inside the
    except block) is kept as state.last_broker_error_detail -- the most
    recent one, not a full log -- purely as context forwarded to the
    self-heal dispatch if check_and_alert_broker_errors() ends up firing."""
    now = time.time()
    state.broker_error_timestamps.append(now)
    if detail:
        state.last_broker_error_detail = detail
    cutoff = now - BROKER_ERROR_WINDOW_SECONDS
    while state.broker_error_timestamps and state.broker_error_timestamps[0] < cutoff:
        state.broker_error_timestamps.pop(0)


def _is_market_hours(asset_class, now_utc=None):
    """Whether `asset_class`'s market is currently in session, DST-aware
    via zoneinfo. Used only to gate the webhook-silence check so it
    doesn't fire when zero webhook hits is completely normal (a closed
    market) rather than a sign anything's broken. Per asset class
    because a single NYSE-only gate here used to mean crypto (a 24/7
    market) was NEVER checked at all and forex went unmonitored for a
    ~16.5-hour stretch every week between its Sunday-evening open and
    Monday's NYSE open:

      - crypto: always in session, no gating -- the market never closes,
        so silence is potentially meaningful at any hour of any day.
      - forex: the real forex trading week, Sunday 17:00 ET through
        Friday 17:00 ET, continuous through weeknights (forex trades
        around the clock between those bounds).
      - stock: NYSE regular session, Mon-Fri 9:30-16:00 ET.
    """
    if asset_class == "crypto":
        return True
    if _NY_TZ is None:
        return True  # no tzdata available -- fail open rather than never alerting
    now_ny = (now_utc or datetime.now(timezone.utc)).astimezone(_NY_TZ)
    weekday = now_ny.weekday()  # Mon=0 .. Sun=6
    minutes_since_midnight = now_ny.hour * 60 + now_ny.minute

    if asset_class == "forex":
        if weekday <= 3:  # Mon-Thu: open around the clock
            return True
        if weekday == 4:  # Friday: open until the 17:00 ET close
            return minutes_since_midnight < 17 * 60
        if weekday == 5:  # Saturday: closed all day
            return False
        return minutes_since_midnight >= 17 * 60  # Sunday: opens 17:00 ET

    # stock: NYSE regular session
    if weekday >= 5:  # Saturday/Sunday
        return False
    return (9 * 60 + 30) <= minutes_since_midnight < (16 * 60)


def check_and_alert_bot_halted(risk_manager):
    if risk_manager.account_halted:
        if not state.alerted_account_halted:
            _post_to_discord(
                "\U0001F6A8 Account-wide trading halted",
                "The account-wide daily loss breaker has tripped -- ALL trading is "
                "stopped across every asset class until the next daily reset.",
            )
            _trigger_github_dispatch("bot-halted", {
                "scope": "account",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "detail": "Account-wide daily loss breaker tripped (risk_manager.account_halted).",
            })
            state.alerted_account_halted = True
    else:
        state.alerted_account_halted = False

    for asset_class in risk_manager.asset_classes:
        halted = risk_manager.trading_halted[asset_class]
        if halted:
            if not state.alerted_trading_halted.get(asset_class):
                _post_to_discord(
                    "\U0001F6A8 {} trading halted".format(asset_class),
                    "{} hit its daily loss limit -- {} trading is stopped for the rest "
                    "of the day. Other asset classes are unaffected.".format(asset_class, asset_class),
                )
                _trigger_github_dispatch("bot-halted", {
                    "scope": asset_class,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "detail": "{} hit its daily loss limit (risk_manager.trading_halted['{}']).".format(
                        asset_class, asset_class
                    ),
                })
                state.alerted_trading_halted[asset_class] = True
        else:
            state.alerted_trading_halted[asset_class] = False


def check_and_alert_webhook_silence():
    """Each SYMBOL is checked independently against its own asset class's
    market hours and its own last-webhook timestamp (state.last_webhook_at
    is per-symbol -- see state.py). Iterates state.watched_symbols (which
    is exactly the set of symbols the bot cares about, kept up to date by
    /api/watchlist) to know which symbols exist and which asset class's
    market hours gate each one. Per-symbol, not per-asset-class, because a
    busy symbol (e.g. NVDA firing every 30m) used to reset a single shared
    per-class clock and mask a DIFFERENT symbol in the SAME class (e.g.
    AAPL) going silent at the same time. Latching is per-symbol too, same
    shape as alerted_trading_halted."""
    now = time.time()
    for asset_class, symbols in state.watched_symbols.items():
        if not _is_market_hours(asset_class):
            continue
        for symbol in symbols:
            last = state.last_webhook_at.get(symbol)
            if last is None:
                continue  # no /webhook hit for this symbol yet this run -- nothing to compare against
            silent_for = now - last
            if silent_for < WEBHOOK_SILENCE_THRESHOLD_SECONDS:
                state.alerted_webhook_silence[symbol] = False
                continue
            if state.alerted_webhook_silence.get(symbol):
                continue
            _post_to_discord(
                "\U0001F507 No {} webhook activity for {:.1f} hours".format(symbol, silent_for / 3600),
                "No /webhook calls for {} have landed in over {:.0f} hours while the {} market "
                "is open -- check that TradingView alerts are still firing and reaching this "
                "server. Other symbols' feeds (even in the same asset class) don't reset this "
                "clock.".format(symbol, WEBHOOK_SILENCE_THRESHOLD_SECONDS / 3600, asset_class),
            )
            _trigger_github_dispatch("webhook-silence", {
                "symbol": symbol,
                "asset_class": asset_class,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "silent_for_hours": round(silent_for / 3600, 2),
                "last_webhook_at": datetime.fromtimestamp(last, tz=timezone.utc).isoformat(),
                "detail": "No /webhook calls for {} received in over {:.0f} hours during {} market hours.".format(
                    symbol, WEBHOOK_SILENCE_THRESHOLD_SECONDS / 3600, asset_class
                ),
            })
            state.alerted_webhook_silence[symbol] = True


def check_and_alert_broker_errors():
    now = time.time()
    cutoff = now - BROKER_ERROR_WINDOW_SECONDS
    while state.broker_error_timestamps and state.broker_error_timestamps[0] < cutoff:
        state.broker_error_timestamps.pop(0)

    count = len(state.broker_error_timestamps)
    if count >= BROKER_ERROR_THRESHOLD:
        if not state.alerted_broker_errors:
            _post_to_discord(
                "⚠️ Repeated broker errors",
                "{} broker errors in the last {:.0f} minutes -- Alpaca or OANDA may be "
                "unreachable or rejecting requests. Check logs for details.".format(
                    count, BROKER_ERROR_WINDOW_SECONDS / 60
                ),
            )
            _trigger_github_dispatch("broker-errors", {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error_count": count,
                "window_minutes": BROKER_ERROR_WINDOW_SECONDS / 60,
                "traceback": state.last_broker_error_detail or "",
            })
            state.alerted_broker_errors = True
    else:
        state.alerted_broker_errors = False
