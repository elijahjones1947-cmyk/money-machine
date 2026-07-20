"""
Tests for the error_log table (db.py) and server.py's DBLogHandler,
which writes every WARNING+ log record app-wide into it so
discord_bot.py (a separate process) has something to read. See
db_store's fixture chain in tests/conftest.py for how server.py gets
imported against a fake Postgres pool.
"""

import db


def test_save_and_get_recent_errors_round_trip(db_store):
    db.save_error_log(level="ERROR", source="brokers.oanda_broker", message="connection timeout")
    rows = db.get_recent_errors(limit=10)
    assert len(rows) == 1
    assert rows[0]["level"] == "ERROR"
    assert rows[0]["source"] == "brokers.oanda_broker"
    assert rows[0]["message"] == "connection timeout"


def test_get_recent_errors_respects_limit_and_newest_first(db_store):
    db.save_error_log(level="WARNING", source="a", message="first")
    db.save_error_log(level="WARNING", source="b", message="second")
    db.save_error_log(level="WARNING", source="c", message="third")
    rows = db.get_recent_errors(limit=2)
    assert len(rows) == 2
    assert [r["message"] for r in rows] == ["third", "second"]


def test_db_log_handler_writes_warning_level_logs_to_error_log(client, db_store):
    """/api/login's failed-attempt path logs a WARNING -- confirms the
    handler attached in server.py actually fires on a real route."""
    resp = client.post("/api/login", json={"password": "wrong"})
    assert resp.status_code == 401
    assert len(db_store["errors"]) >= 1
    assert any("Failed dashboard login attempt" in e["message"] for e in db_store["errors"])
    assert all(e["level"] in ("WARNING", "ERROR") for e in db_store["errors"])


def test_db_log_handler_does_not_write_info_level_logs(client, db_store):
    """/webhook logs an INFO-level 'Webhook received' line on every hit
    -- that should never reach error_log (handler is WARNING+ only)."""
    import webhook_queue

    client.post("/webhook", json={"secret": "test-webhook-secret", "action": "buy", "symbol": "AAPL"})
    # /webhook now queues the actual trade in the background (see
    # server.py's webhook() docstring) -- wait for it to finish before
    # this test returns, or it can still be running when the next
    # test's state-reset fixture fires.
    webhook_queue.wait_for_idle("AAPL")
    assert not any("Webhook received" in e["message"] for e in db_store["errors"])


def test_db_log_handler_swallows_db_write_failures(client, db_store, monkeypatch):
    """A DB write failure inside the handler must never surface as a
    request failure -- the whole point of catching broadly in emit()."""
    import db as db_module

    def _boom(**kwargs):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(db_module, "save_error_log", _boom)

    resp = client.post("/api/login", json={"password": "wrong"})
    assert resp.status_code == 401  # request still succeeds despite the handler's DB write failing
