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

import pytest

from brokers.oanda_broker import OandaBroker
from errors import BrokerConnectionError, InsufficientFundsError


def _make_broker():
    return OandaBroker(api_key="test-key", account_id="test-account", base_url="https://api-fxpractice.oanda.com")


class _FakeCandlesResponse:
    status_code = 200

    def json(self):
        return {"candles": []}


def test_get_ohlcv_maps_30m_to_oanda_m30_granularity(monkeypatch):
    """The gap this closes: forex strategies now report a real 30m
    timeframe (server.py's _OBSERVED_LIVE_STRATEGY_TIMEFRAMES), and
    get_asset_market_data (a Hermes tool) can be asked for an arbitrary
    timeframe -- "30m" must actually be a supported OANDA granularity,
    not just stock/crypto's (Alpaca's _TIMEFRAME_MAP already had it --
    see test_alpaca_broker.py's confirming test). Live incident: Hermes
    asked for 30m GBP_JPY bars and got ValueError: Unsupported timeframe."""
    broker = _make_broker()
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return _FakeCandlesResponse()

    monkeypatch.setattr(broker.session, "get", fake_get)

    bars = broker.get_ohlcv("GBP_JPY", timeframe="30m", limit=50)

    assert bars == []  # no candles in the fake response, but no error either
    assert captured["params"]["granularity"] == "M30"


def test_get_ohlcv_still_rejects_a_genuinely_unsupported_timeframe():
    """Regression guard for the ValueError path itself -- adding 30m
    must not turn this into a silent pass-through for anything."""
    broker = _make_broker()
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        broker.get_ohlcv("GBP_JPY", timeframe="2h", limit=50)


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


# --- 401-specific retry (distinct from the connection/read-timeout retry
# above -- these are real HTTP 401 responses OANDA itself sends, observed
# in production to be intermittent and self-clearing) --------------------

class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def test_get_retries_once_on_401_and_succeeds_on_the_retry(monkeypatch):
    broker = _make_broker()
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            return _FakeResponse(401, {"errorMessage": "Insufficient authorization to perform request."})
        return _FakeResponse(200, {"account": {"NAV": "10000", "marginAvailable": "9000", "unrealizedPL": "0"}})

    monkeypatch.setattr(broker.session, "get", fake_get)
    monkeypatch.setattr("brokers.oanda_broker.time.sleep", lambda s: None)

    info = broker.get_account_info()

    assert len(calls) == 2  # one failed attempt, one retry
    assert info["equity"] == 10000.0


def test_get_gives_up_after_one_retry_if_still_401(monkeypatch):
    broker = _make_broker()
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(1)
        return _FakeResponse(401, {"errorMessage": "Insufficient authorization to perform request."})

    monkeypatch.setattr(broker.session, "get", fake_get)
    monkeypatch.setattr("brokers.oanda_broker.time.sleep", lambda s: None)

    with pytest.raises(BrokerConnectionError, match="401"):
        broker.get_account_info()

    assert len(calls) == 2  # exactly one retry, not a retry loop


def test_put_also_retries_once_on_401(monkeypatch):
    broker = _make_broker()
    calls = []

    def fake_put(url, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            return _FakeResponse(401, {"errorMessage": "Insufficient authorization to perform request."})
        return _FakeResponse(200, {"status": "cancelled"})

    monkeypatch.setattr(broker.session, "put", fake_put)
    monkeypatch.setattr("brokers.oanda_broker.time.sleep", lambda s: None)

    result = broker.cancel_order("123")

    assert len(calls) == 2
    assert result == {"status": "cancelled"}


def test_place_order_never_retries_on_401_even_transiently(monkeypatch):
    """The one call site that must NOT get the 401 retry: place_order
    posts directly via self.session.post, bypassing _get/_put entirely
    -- a request that already reached OANDA and placed an order must
    never risk becoming a duplicate via a blind retry."""
    broker = _make_broker()
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(1)
        return _FakeResponse(401, {"errorMessage": "Insufficient authorization to perform request."})

    monkeypatch.setattr(broker.session, "post", fake_post)
    monkeypatch.setattr("brokers.oanda_broker.time.sleep", lambda s: (_ for _ in ()).throw(AssertionError("place_order must never sleep/retry")))

    with pytest.raises(BrokerConnectionError):
        broker.place_order("GBP_JPY", "buy", 1000)

    assert len(calls) == 1  # no retry at all


# --- _translate_error: INSUFFICIENT_AUTHORIZATION must not be classified
# as InsufficientFundsError (a "not enough money" rejection) -- that
# silently skips the BrokerConnectionError handling in server.py that
# actually alerts on broker errors (alerts.record_broker_error). -------

def test_insufficient_authorization_is_a_connection_error_not_insufficient_funds():
    broker = _make_broker()
    with pytest.raises(BrokerConnectionError):
        broker._translate_error(401, {"errorCode": "INSUFFICIENT_AUTHORIZATION"}, "GBP_JPY")


def test_insufficient_margin_is_unaffected_by_the_authorization_fix():
    broker = _make_broker()
    with pytest.raises(InsufficientFundsError):
        broker._translate_error(400, {"errorCode": "INSUFFICIENT_MARGIN"}, "GBP_JPY")
