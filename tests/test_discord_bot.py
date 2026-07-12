"""
Tests for discord_bot.py's pure/mockable logic: diagnostic context
gathering (health snapshot + recent error_log rows, via the same fake-DB
fixtures tests/conftest.py already provides) and the GitHub Actions
run-history fetch (requests mocked, same pattern as
tests/test_alerts.py's GitHub dispatch tests).

NOT covered here: discord.Client, on_message, or client.run(). A
discord.Client owns a real websocket gateway connection (login
handshake, heartbeat loop, reconnect/backoff logic, rate-limit
handling) that discord.py implements in C-extension-backed internals --
there's no supported way to exercise it without either a live Discord
connection or hand-rolling a fake gateway server, and mocking
discord.Message/TextChannel deeply enough to drive on_message() would
mostly be testing the mocks, not discord_bot.py's own logic. All of the
actual bug/error/health logic (what gets gathered, how GitHub API
scope failures are reported, what gets sent to Claude) lives in the
plain functions below and IS covered; only the thin gateway glue in
create_discord_client()/main() is skipped.
"""

import config
import discord_bot


def test_get_health_snapshot_reads_persisted_setting(db_store):
    import db
    db.save_setting("health_snapshot", {"account_halted": True, "trading_halted": {"stock": False}})
    result = discord_bot.get_health_snapshot()
    assert result["account_halted"] is True
    assert result["trading_halted"] == {"stock": False}


def test_get_health_snapshot_empty_dict_when_never_persisted(db_store):
    assert discord_bot.get_health_snapshot() == {}


def test_get_recent_error_log_reads_error_log_table(db_store):
    import db
    db.save_error_log(level="WARNING", source="server", message="something broke")
    rows = discord_bot.get_recent_error_log(limit=5)
    assert len(rows) == 1
    assert rows[0]["level"] == "WARNING"
    assert rows[0]["source"] == "server"
    assert rows[0]["message"] == "something broke"


def test_get_recent_self_heal_runs_without_token(monkeypatch, db_store):
    monkeypatch.setattr(config, "GITHUB_DISPATCH_TOKEN", "")
    result = discord_bot.get_recent_self_heal_runs()
    assert result["runs"] == []
    assert "not configured" in result["note"]


def test_get_recent_self_heal_runs_reports_insufficient_scope(monkeypatch, db_store):
    import requests

    class FakeResp:
        status_code = 403
        text = "Resource not accessible by personal access token"

    monkeypatch.setattr(config, "GITHUB_DISPATCH_TOKEN", "fake-token")
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp())

    result = discord_bot.get_recent_self_heal_runs()
    assert result["runs"] == []
    assert "Actions: Read" in result["note"]


def test_get_recent_self_heal_runs_success(monkeypatch, db_store):
    import requests

    class FakeResp:
        status_code = 200

        def json(self):
            return {"workflow_runs": [
                {
                    "run_number": 12, "status": "completed", "conclusion": "success",
                    "event": "repository_dispatch", "created_at": "2026-07-12T00:00:00Z",
                    "html_url": "https://github.com/x/y/actions/runs/1",
                },
            ]}

    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return FakeResp()

    monkeypatch.setattr(config, "GITHUB_DISPATCH_TOKEN", "fake-token")
    monkeypatch.setattr(requests, "get", fake_get)

    result = discord_bot.get_recent_self_heal_runs()
    assert result["note"] is None
    assert result["runs"][0]["run_number"] == 12
    assert result["runs"][0]["conclusion"] == "success"
    assert "self-heal.yml" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer fake-token"


def test_get_recent_self_heal_runs_network_failure(monkeypatch, db_store):
    import requests

    def fake_get(*a, **k):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(config, "GITHUB_DISPATCH_TOKEN", "fake-token")
    monkeypatch.setattr(requests, "get", fake_get)

    result = discord_bot.get_recent_self_heal_runs()
    assert result["runs"] == []
    assert "Could not reach GitHub" in result["note"]


def test_build_diagnostic_context_combines_all_sources(monkeypatch, db_store):
    import db
    import requests

    db.save_setting("health_snapshot", {"account_halted": False})
    db.save_error_log(level="ERROR", source="brokers.alpaca_broker", message="rate limited")
    monkeypatch.setattr(config, "GITHUB_DISPATCH_TOKEN", "")

    context = discord_bot.build_diagnostic_context()

    assert context["health_snapshot"]["account_halted"] is False
    assert context["recent_errors"][0]["message"] == "rate limited"
    assert context["recent_self_heal_runs"]["runs"] == []
    # Never present, by design -- see discord_bot.py's module docstring.
    assert "portfolio" not in context
    assert "positions" not in context
    assert "trades" not in context


def test_ask_claude_sends_system_prompt_and_context_with_question():
    captured = {}

    class FakeTextBlock:
        type = "text"
        text = "everything looks healthy"

    class FakeResponse:
        content = [FakeTextBlock()]

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return FakeResponse()

    class FakeAnthropicClient:
        messages = FakeMessages()

    reply = discord_bot.ask_claude(FakeAnthropicClient(), "is anything broken?", {"recent_errors": []})

    assert reply == "everything looks healthy"
    assert captured["system"] == discord_bot.SYSTEM_PROMPT
    assert "is anything broken?" in captured["messages"][0]["content"]
    assert "recent_errors" in captured["messages"][0]["content"]


def test_main_requires_discord_token_and_channel_id(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_BOT_TOKEN", "")
    monkeypatch.setattr(config, "DISCORD_ALERTS_CHANNEL_ID", "")
    import pytest
    with pytest.raises(RuntimeError, match="DISCORD_BOT_TOKEN"):
        discord_bot.main()


def test_main_requires_anthropic_key(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_BOT_TOKEN", "fake-token")
    monkeypatch.setattr(config, "DISCORD_ALERTS_CHANNEL_ID", "12345")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    import pytest
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        discord_bot.main()
