"""
Tests for OandaBroker's HTTP session configuration -- specifically the
retry-on-connection/read-timeout adapter added after a real production
incident (2026-07-12): a long-lived requests.Session reused for the
life of the gunicorn worker intermittently hit stale pooled connections
that hung until the timeout, surfacing as persistent "Read timed out"
errors even though OANDA's API itself was responding in well under a
second. See brokers/oanda_broker.py's __init__ docstring for the full
diagnosis.

No real network calls here -- constructing an OandaBroker only sets
headers and mounts adapters, so this is as safe/cheap to test directly
as the pure-logic modules (unlike server.py -- see tests/conftest.py's
docstring for why THAT needs a fake DB pool and fake broker instances
instead of importing brokers directly).
"""

from brokers.oanda_broker import OandaBroker


def _make_broker():
    return OandaBroker(api_key="test-key", account_id="test-account", base_url="https://api-fxpractice.oanda.com")


def test_session_retries_once_on_connection_and_read_timeouts():
    broker = _make_broker()
    retry = broker.session.adapters["https://"].max_retries
    assert retry.total == 1
    assert retry.connect == 1
    assert retry.read == 1


def test_session_never_retries_based_on_http_status_code():
    """_translate_error already handles OANDA's own structured error
    responses (insufficient margin, invalid instrument, ...) -- retrying
    on status codes would be redundant at best and could mask a real
    rejection as a transient failure at worst."""
    broker = _make_broker()
    retry = broker.session.adapters["https://"].max_retries
    assert not retry.status_forcelist


def test_session_retry_excludes_post_but_allows_get_and_put():
    """place_order (POST) must never be auto-retried: if the original
    request actually reached OANDA and placed an order but the response
    was lost to the same timeout, a blind retry would risk a duplicate
    order. cancel_order (PUT) and every read call (GET) are idempotent
    and safe to retry."""
    broker = _make_broker()
    retry = broker.session.adapters["https://"].max_retries
    allowed = retry.allowed_methods
    assert "POST" not in allowed
    assert "GET" in allowed
    assert "PUT" in allowed


def test_http_and_https_use_the_same_retrying_adapter():
    broker = _make_broker()
    assert broker.session.adapters["http://"].max_retries.total == 1
