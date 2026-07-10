"""
Hermes: a Claude-powered chat agent with read access to the bot's live
state (portfolio, positions, trades, regime, risk, config, market data)
and a small set of executor tools that can change bot behavior
(pause/resume trading, adjust a risk limit).

Safety model: read tools execute the moment the model calls them.
Executor tools NEVER auto-execute — the first executor tool_use in any
response is staged as a pending_action and the turn stops there; the
user must explicitly confirm via /api/hermes/confirm before it runs.
(If the model asks for a read tool and an executor in the same
response, only tool_use blocks up to and including the first executor
call are handled this turn — see _run_turn.)

State here is in-process only (conversation history + any pending
action), same tradeoff state.py already makes — fine under the current
single-worker gunicorn config (see the Procfile), NOT fine if this ever
runs with workers > 1 without moving to a real shared store (Postgres,
Redis, etc.) first.
"""

import logging

from flask import Blueprint, jsonify, request, session

import config
from hermes_tools import EXECUTOR_TOOL_NAMES, TOOL_FUNCTIONS, TOOL_SCHEMAS, HermesContext

hermes_bp = Blueprint("hermes", __name__, url_prefix="/api/hermes")

MODEL = "claude-sonnet-5"
MAX_TOKENS = 1024

SYSTEM_PROMPT = """You are Hermes, an assistant embedded in a live automated trading bot
(stocks/crypto via Alpaca, forex via OANDA). You have read-only tools to check
portfolio status, positions, trade history, market regime, risk state, strategy
config, and market data, plus a few tools that change bot behavior (pause/resume
trading, adjust a risk limit) — those always require the user's explicit
confirmation before they take effect, which happens automatically; you don't need
to ask twice, just call the tool and explain what you're proposing.

Be direct and specific — cite real numbers from the tools rather than general
trading commentary. If a tool reports missing data or an error, say so plainly
instead of guessing. This bot trades real (or paper) money — don't downplay risk,
and don't recommend specific trades; you can describe what's happening and what
the data shows, but leave trading decisions to the person you're talking to.

When asked for a "daily summary" (or similar — health check, how did today go),
call get_daily_summary first and build your answer directly from its numbers:
say plainly whether the bot is enabled and whether anything is halted, then
walk through today's net P&L and win/loss count, calling out which symbols
drove gains or losses. If today has zero trades, say so and don't speculate
about why unless the tool data suggests a reason (e.g. everything halted)."""

# Module state — see the docstring's single-worker caveat.
_ctx = None
_client = None
_conversation = []  # raw Anthropic-format messages, fed back on every turn
_transcript = []  # simplified {role, text} for the frontend to render
_pending_action = None  # {tool_name, tool_input, tool_use_id} awaiting confirm


def init_hermes(alpaca_broker, oanda_broker, risk_manager):
    """Called once from server.py after the real brokers/risk_manager
    exist. Hermes stays disabled (routes return 503) if no API key is
    configured, rather than the whole app failing to boot over an
    optional feature."""
    global _ctx, _client
    _ctx = HermesContext(alpaca_broker, oanda_broker, risk_manager)
    if config.ANTHROPIC_API_KEY:
        import anthropic
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    else:
        logging.warning("Hermes: ANTHROPIC_API_KEY not set — chat routes will return 503.")


def _require_auth():
    return session.get("auth")


def _require_ready():
    return _client is not None and _ctx is not None


def _execute_tool(name, tool_input):
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": "unknown tool: {}".format(name)}
    try:
        return fn(_ctx, **tool_input)
    except Exception as e:
        logging.exception("Hermes tool {} failed".format(name))
        return {"error": "tool execution failed: {}".format(e)}


def _extract_text(message):
    return "".join(block.text for block in message.content if block.type == "text").strip()


def _run_turn():
    """Calls the model, executing read tools as they come up. Stops and
    stages a pending_action the moment an executor tool_use appears —
    does not call the API again until that's confirmed/rejected. Mutates
    _conversation in place. Returns the reply text (may be empty if the
    turn ended on a staged executor with no accompanying text)."""
    global _pending_action

    while True:
        response = _client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
            messages=_conversation, tools=TOOL_SCHEMAS,
        )
        assistant_content = [block.model_dump() for block in response.content]
        _conversation.append({"role": "assistant", "content": assistant_content})

        if response.stop_reason != "tool_use":
            return _extract_text(response)

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        executor_block = next((b for b in tool_use_blocks if b.name in EXECUTOR_TOOL_NAMES), None)

        if executor_block is not None:
            _pending_action = {
                "tool_name": executor_block.name,
                "tool_input": executor_block.input,
                "tool_use_id": executor_block.id,
            }
            text = _extract_text(response)
            return text or "I'd like to {} — confirm to proceed.".format(executor_block.name)

        # No executor this round — run every read tool and feed results back.
        tool_results = []
        for block in tool_use_blocks:
            result = _execute_tool(block.name, block.input)
            tool_results.append({
                "type": "tool_result", "tool_use_id": block.id,
                "content": _to_text(result),
            })
        _conversation.append({"role": "user", "content": tool_results})
        # loop continues, calling the model again with the tool results


def _to_text(result):
    import json
    return json.dumps(result, default=str)


def _record(role, text):
    _transcript.append({"role": role, "text": text})


@hermes_bp.route("/chat", methods=["POST"])
def chat():
    if not _require_auth():
        return jsonify({"error": "unauthorized"}), 401
    if not _require_ready():
        return jsonify({"error": "Hermes is not configured (missing ANTHROPIC_API_KEY)"}), 503
    if _pending_action is not None:
        return jsonify({"error": "A pending action is awaiting confirmation — resolve it first", "pending_action": _pending_action}), 409

    data = request.json or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    _conversation.append({"role": "user", "content": message})
    _record("user", message)

    try:
        reply = _run_turn()
    except Exception as e:
        logging.exception("Hermes chat turn failed")
        return jsonify({"error": "Hermes hit an error: {}".format(e)}), 500

    _record("assistant", reply)
    return jsonify({"reply": reply, "pending_action": _pending_action})


@hermes_bp.route("/confirm", methods=["POST"])
def confirm():
    global _pending_action
    if not _require_auth():
        return jsonify({"error": "unauthorized"}), 401
    if not _require_ready():
        return jsonify({"error": "Hermes is not configured"}), 503
    if _pending_action is None:
        return jsonify({"error": "no pending action"}), 400

    data = request.json or {}
    approved = bool(data.get("confirm"))
    action = _pending_action
    _pending_action = None

    if approved:
        result = _execute_tool(action["tool_name"], action["tool_input"])
    else:
        result = {"cancelled_by_user": True}

    _conversation.append({
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": action["tool_use_id"], "content": _to_text(result)}],
    })

    try:
        reply = _run_turn()
    except Exception as e:
        logging.exception("Hermes confirm turn failed")
        return jsonify({"error": "Hermes hit an error: {}".format(e)}), 500

    _record("assistant", reply)
    return jsonify({"reply": reply, "executed": approved, "result": result, "pending_action": _pending_action})


@hermes_bp.route("/history", methods=["GET"])
def history():
    if not _require_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"transcript": _transcript, "pending_action": _pending_action, "configured": _require_ready()})
