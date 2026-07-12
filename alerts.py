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
BROKER_ERROR_WINDOW_SECONDS = 15 * 60  # 15 minutes
BROKER_ERROR_THRESHOLD = 5  # >= this many broker errors within the window above triggers an alert


def _post_to_discord(title, description):
    webhook_url = config.DISCORD_ALERT_WEBHOOK_URL
    if not webhook_url:
        return
    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": 0xE74C3C,  # red -- every alert here is a "something's wrong" condition
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=_DISCORD_TIMEOUT_SECONDS)
        if resp.status_code >= 300:
            logging.warning("Discord alert post failed ({}): {}".format(resp.status_code, resp.text[:200]))
    except requests.RequestException as e:
        logging.warning("Discord alert post failed: {}".format(e))


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


def _is_market_hours(now_utc=None):
    """US stock market regular session (Mon-Fri, 9:30-16:00
    America/New_York), DST-aware via zoneinfo. Deliberate simplification:
    forex and crypto trade far more hours than this. It's used only to
    gate the webhook-silence check so that check doesn't fire every
    single weekend/overnight, when zero webhook hits is completely
    normal rather than a sign anything's broken. A real forex/crypto-only
    problem during stock off-hours won't be caught by this check --
    flagging that gap rather than pretending this covers every asset
    class's hours."""
    if _NY_TZ is None:
        return True  # no tzdata available -- fail open rather than never alerting
    now_ny = (now_utc or datetime.now(timezone.utc)).astimezone(_NY_TZ)
    if now_ny.weekday() >= 5:  # Saturday/Sunday
        return False
    minutes_since_midnight = now_ny.hour * 60 + now_ny.minute
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
    if not _is_market_hours():
        return
    if state.last_webhook_at is None:
        return  # no /webhook hit yet this run -- nothing to compare against
    silent_for = time.time() - state.last_webhook_at
    if silent_for < WEBHOOK_SILENCE_THRESHOLD_SECONDS:
        return
    if not state.alerted_webhook_silence:
        _post_to_discord(
            "\U0001F507 No webhook activity for {:.1f} hours".format(silent_for / 3600),
            "No /webhook calls have landed in over {:.0f} hours during market hours -- "
            "check that TradingView alerts are still firing and reaching this server.".format(
                WEBHOOK_SILENCE_THRESHOLD_SECONDS / 3600
            ),
        )
        _trigger_github_dispatch("webhook-silence", {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "silent_for_hours": round(silent_for / 3600, 2),
            "last_webhook_at": datetime.fromtimestamp(state.last_webhook_at, tz=timezone.utc).isoformat(),
            "detail": "No /webhook calls received in over {:.0f} hours during market hours.".format(
                WEBHOOK_SILENCE_THRESHOLD_SECONDS / 3600
            ),
        })
        state.alerted_webhook_silence = True


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
