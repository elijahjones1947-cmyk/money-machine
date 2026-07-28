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


# --- GET /api/errors (dashboard's recent-errors card) ----------------------

def test_api_errors_requires_auth(client):
    assert client.get("/api/errors").status_code == 401


def test_api_errors_returns_recent_rows_newest_first(auth_client, db_store):
    db.save_error_log(level="WARNING", source="a", message="first real issue")
    db.save_error_log(level="ERROR", source="b", message="second real issue")

    resp = auth_client.get("/api/errors")
    assert resp.status_code == 200
    errors = resp.get_json()["errors"]
    assert [e["message"] for e in errors] == ["second real issue", "first real issue"]
    assert errors[0]["level"] == "ERROR"
    assert errors[0]["source"] == "b"
    assert "occurred_at" in errors[0]


def test_api_errors_respects_limit_query_param(auth_client, db_store):
    for i in range(5):
        db.save_error_log(level="WARNING", source="x", message="issue {}".format(i))
    resp = auth_client.get("/api/errors?limit=2")
    assert len(resp.get_json()["errors"]) == 2


def test_api_errors_excludes_dust_position_noise(auth_client, db_store):
    """The exact triplet the SOL/USD incident produced (see
    run_position_safety_checks()'s docstring) must never drown out real
    issues in this feed -- the two unambiguous dust signatures
    (server.py's _is_dust_noise) are excluded, a real error is not."""
    db.save_error_log(
        level="ERROR", source="root",
        message="SAFETY NET: crypto SOL/USD unrealized loss 7.03% >= 5.00% threshold -- force-closing (sell 5.45e-07)",
    )
    db.save_error_log(
        level="WARNING", source="root",
        message="Rejected sell crypto SOL/USD: Alpaca: computed quantity <= 0 for SOL/USD",
    )
    db.save_error_log(
        level="ERROR", source="root",
        message="Safety-net force-close for SOL/USD did not succeed (status 400)",
    )
    db.save_error_log(
        level="ERROR", source="root",
        message="Intrabar force-close for ETH/USD did not succeed (status 400)",
    )
    db.save_error_log(level="ERROR", source="root", message="OANDA account fetch failed: timeout")

    errors = auth_client.get("/api/errors").get_json()["errors"]
    messages = [e["message"] for e in errors]
    assert "OANDA account fetch failed: timeout" in messages
    assert not any("computed quantity <= 0" in m for m in messages)
    assert not any("Safety-net force-close" in m and "did not succeed" in m for m in messages)
    assert not any("Intrabar force-close" in m and "did not succeed" in m for m in messages)
    # The trigger line itself (not paired with "did not succeed") is a
    # real, if noisy, force-close attempt -- not excluded on its own.
    assert any("SAFETY NET" in m for m in messages)


def test_api_errors_does_not_hide_a_real_strategy_switch_force_close_failure(client):
    """A force-close failing during an operator-initiated strategy
    switch is a genuinely different, rare, non-dust event (see
    server.py's api_assign_strategy docstring) -- must never be
    caught by the 'force-close for ... did not succeed' dust pattern,
    which is deliberately scoped to the safety-net/intrabar wording
    only. `client` fixture only used to guarantee server.py is imported
    via app_module's controlled setup (patched brokers, stopped
    scheduler) before this reaches into it directly."""
    import server

    assert not server._is_dust_noise(
        "STRATEGY SWITCH for AAPL ABORTED: force-close did not succeed (status 400) -- assignment NOT changed."
    )
