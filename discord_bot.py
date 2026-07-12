"""
Discord bug/error/health Q&A bot -- runs as its OWN process (see the
Procfile's "worker" entry: `worker: python discord_bot.py`), completely
separate from the Flask app (server.py) and from Hermes
(hermes.py/hermes_tools.py). It can crash, restart, redeploy, or be
scaled independently without touching trading, and a bug in this file
can never affect order placement.

WHAT THIS BOT HAS ACCESS TO (read-only, gathered fresh per question):
  - error_log (db.py): the app's recent WARNING+ log records, written
    by server.py's DBLogHandler.
  - health_snapshot (db.py's bot_settings table): risk_manager's
    current halt state (account_halted, trading_halted per asset
    class), failed_login_attempts/failed_webhook_attempts counts, and
    time since the last /webhook hit -- written by server.py's
    _persist_health_snapshot(), refreshed every 5 minutes (the same
    cycle as alerts.py's checks). This process has NO direct access to
    the Flask process's in-memory risk_manager/state.py objects
    (separate OS process, separate Procfile entry) -- Postgres is the
    handoff point, same pattern as every other piece of persisted
    state in this app.
  - Recent GitHub Actions runs of .github/workflows/self-heal.yml, via
    GitHub's REST API. See get_recent_self_heal_runs()'s docstring for
    the GITHUB_DISPATCH_TOKEN scope caveat.

WHAT THIS BOT DOES NOT HAVE ACCESS TO, ON PURPOSE:
  - Portfolio value, positions, P&L, trade history, or backtest/
    strategy performance -- that's Hermes's job, scoped to the
    dashboard's /api/hermes/* routes. This bot's SYSTEM_PROMPT
    explicitly refuses those questions and points the user to Hermes
    instead. hermes.py and hermes_tools.py are NOT imported or
    modified by this file -- the two assistants are fully independent,
    sharing only the same ANTHROPIC_API_KEY and Postgres database.
  - Any executor/write capability. Every function below is read-only;
    this bot can never place a trade, change a setting, pause/resume
    the bot, or touch anything hermes_tools.py's executor tools touch.

Requires DISCORD_BOT_TOKEN, DISCORD_ALERTS_CHANNEL_ID, and
ANTHROPIC_API_KEY (all read via config.py) -- see main() for what
happens if any are missing.
"""

import asyncio
import json
import logging

import discord
import requests

import alerts
import config
import db

MODEL = "claude-sonnet-5"
MAX_TOKENS = 1024
_GITHUB_TIMEOUT_SECONDS = 10
_DISCORD_MESSAGE_LIMIT = 2000  # Discord hard-rejects longer messages

SYSTEM_PROMPT = """You are a diagnostic assistant for the "money-machine" automated trading
bot, answering questions in its Discord alerts channel. You have READ-ONLY access to
bug/error/health data ONLY: the app's recent WARNING+ log entries, the risk manager's
current halt state (account-wide and per asset class), failed dashboard-login and
failed-webhook attempt counts, time since the last /webhook hit, and recent GitHub
Actions runs of the self-heal workflow. All of this is handed to you as JSON context
with each question -- don't assume anything beyond what's in it.

You do NOT have, and must never claim to have, access to portfolio value, positions,
P&L, trade history, or strategy/backtest performance. That is Hermes's job -- a SEPARATE
assistant embedded in the dashboard with its own tools. If asked about portfolio, P&L,
trades, positions, or strategy/backtest performance, say plainly that you don't cover
that and tell the user to ask Hermes in the dashboard instead. Do not attempt to answer
those questions even partially, and do not speculate about portfolio/trade data.

Answer only what the provided diagnostic context supports. If the context doesn't
contain enough to answer a bug/error/health question, say so plainly rather than
guessing. Be direct and cite real numbers/timestamps from the context rather than vague
reassurance. Remember 'last_webhook_at' reflects ANY inbound /webhook call, not just
authenticated ones, and 'health_snapshot' data can be up to ~5 minutes stale."""


def get_health_snapshot():
    """See server.py's _persist_health_snapshot() for what writes this
    and how fresh it is."""
    return db.get_setting("health_snapshot", default={})


def get_recent_error_log(limit=15):
    """Recent WARNING+ log records across the whole Flask app, written
    by server.py's DBLogHandler."""
    rows = db.get_recent_errors(limit=limit)
    return [
        {
            "occurred_at": str(r["occurred_at"]),
            "level": r["level"],
            "source": r["source"],
            "message": r["message"],
        }
        for r in rows
    ]


def get_recent_self_heal_runs(limit=5):
    """Recent GitHub Actions runs of .github/workflows/self-heal.yml, via
    GitHub's REST API. Reuses GITHUB_DISPATCH_TOKEN (config.py) -- the
    same token alerts.py already uses to fire the repository_dispatch
    events that trigger this workflow -- rather than provisioning a
    second GitHub credential just for this bot.

    That token's scope was chosen for alerts.py's need: firing
    repository_dispatch, which needs `repo` on a classic PAT or
    "Contents: Read and write" on a fine-grained one (see config.py's
    comment on GITHUB_DISPATCH_TOKEN). Reading Actions run history is a
    DIFFERENT permission -- `Actions: Read` on a fine-grained PAT
    (already covered by `repo` on a classic PAT). If the configured
    token doesn't have it, this returns a clear note explaining exactly
    that instead of silently guessing or crashing.
    """
    token = config.GITHUB_DISPATCH_TOKEN
    if not token:
        return {"runs": [], "note": "GITHUB_DISPATCH_TOKEN is not configured -- can't read GitHub Actions run history."}

    url = "https://api.github.com/repos/{}/actions/workflows/self-heal.yml/runs".format(alerts.GITHUB_REPO)
    headers = {
        "Authorization": "Bearer {}".format(token),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        resp = requests.get(url, headers=headers, params={"per_page": limit}, timeout=_GITHUB_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        return {"runs": [], "note": "Could not reach GitHub's API: {}".format(e)}

    if resp.status_code in (401, 403):
        return {
            "runs": [],
            "note": (
                "GitHub API returned {} reading self-heal.yml's run history -- GITHUB_DISPATCH_TOKEN likely "
                "doesn't have the 'Actions: Read' permission (fine-grained PAT) or equivalent scope (classic "
                "PAT missing 'repo'). It was provisioned for firing repository_dispatch events (alerts.py), "
                "which is a different permission. Add Actions: Read to the token to enable this.".format(resp.status_code)
            ),
        }
    if resp.status_code >= 300:
        return {"runs": [], "note": "GitHub API returned {}: {}".format(resp.status_code, resp.text[:200])}

    runs = resp.json().get("workflow_runs", [])
    return {
        "runs": [
            {
                "run_number": r["run_number"],
                "status": r["status"],
                "conclusion": r["conclusion"],
                "event": r["event"],
                "created_at": r["created_at"],
                "html_url": r["html_url"],
            }
            for r in runs[:limit]
        ],
        "note": None,
    }


def build_diagnostic_context():
    """Everything this bot is allowed to know, gathered fresh for one
    question. Deliberately does NOT include anything about portfolio,
    positions, P&L, or trades -- see this module's docstring."""
    return {
        "health_snapshot": get_health_snapshot(),
        "recent_errors": get_recent_error_log(limit=15),
        "recent_self_heal_runs": get_recent_self_heal_runs(limit=5),
    }


def ask_claude(anthropic_client, user_message, context):
    """Sends the diagnostic context + the user's question to Claude
    under SYSTEM_PROMPT's bug/error/health-only scope, returns the
    reply text."""
    response = anthropic_client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": "Diagnostic context (JSON):\n{}\n\nQuestion: {}".format(
                json.dumps(context, default=str), user_message
            ),
        }],
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()


def create_discord_client(anthropic_client, channel_id):
    """Builds (but doesn't run) the discord.Client. Kept separate from
    main() so it's constructible in isolation if ever needed -- the
    on_message handler is thin glue: gate on channel, gather context,
    ask Claude, reply. All the actual logic lives in the plain
    functions above, which is also where the test coverage is (see
    tests/test_discord_bot.py's module docstring for why the gateway
    glue itself isn't unit-tested)."""
    intents = discord.Intents.default()
    intents.message_content = True  # privileged intent -- must also be enabled in the Discord Developer Portal
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        logging.info("discord_bot: logged in as {}".format(client.user))

    @client.event
    async def on_message(message):
        if message.author.bot or message.channel.id != channel_id:
            return
        async with message.channel.typing():
            try:
                context = await asyncio.to_thread(build_diagnostic_context)
                reply = await asyncio.to_thread(ask_claude, anthropic_client, message.content, context)
            except Exception:
                logging.exception("discord_bot: failed to answer message")
                reply = "Something went wrong gathering diagnostics or asking Claude -- check this process's logs."
        await message.channel.send(reply[:_DISCORD_MESSAGE_LIMIT])

    return client


def main():
    logging.basicConfig(level=logging.INFO)

    if not config.DISCORD_BOT_TOKEN or not config.DISCORD_ALERTS_CHANNEL_ID:
        raise RuntimeError(
            "discord_bot.py requires both DISCORD_BOT_TOKEN and DISCORD_ALERTS_CHANNEL_ID to be set "
            "(config.py leaves them optional so importing config.py never breaks the Flask app in "
            "server.py, which needs neither) -- set them as Railway env vars, or in a local .env for "
            "testing, before running this process."
        )
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("discord_bot.py requires ANTHROPIC_API_KEY to be set (the same key Hermes uses).")

    channel_id = int(config.DISCORD_ALERTS_CHANNEL_ID)

    # This process' own Postgres pool -- separate from server.py's (a
    # different OS process). init_schema() is idempotent (CREATE TABLE
    # IF NOT EXISTS), so calling it here too guarantees error_log/etc.
    # exist even if this process happens to start before server.py's.
    db.init_pool()
    db.init_schema()

    import anthropic  # lazy import, same convention as hermes.py's init_hermes()
    anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    client = create_discord_client(anthropic_client, channel_id)
    client.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
