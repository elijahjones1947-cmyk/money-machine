"""
Tests for alerts.py's pure logic: the market-hours gate and the three
edge-triggered/latched check_and_alert_* functions. No Flask/DB/broker
dependency (alerts.py only needs config + state), so these import
directly like the other pure-logic test modules.

_post_to_discord never fires a real network call here: conftest.py
forces DISCORD_ALERT_WEBHOOK_URL="" before config.py is ever imported,
specifically so test runs can never post to a real Discord channel even
if a local .env has a real webhook URL in it (see conftest.py's comment
next to that env var).
"""

import time
from datetime import datetime, timezone

import pytest

import alerts
import config
import state


@pytest.fixture(autouse=True)
def clean_alert_state():
    """alerts.py's latches/rolling log live on the shared state module --
    reset before every test so they can't leak between tests. Also pins
    watched_symbols to a known set (check_and_alert_webhook_silence
    iterates it to know which symbols/asset classes to check) so these
    tests don't depend on whatever another test file's /api/watchlist
    calls may have appended to the real module-level dict earlier in
    the same pytest session."""
    state.alerted_account_halted = False
    state.alerted_trading_halted = {"stock": False, "forex": False, "crypto": False}
    state.alerted_webhook_silence = {}
    state.alerted_broker_errors = False
    state.broker_error_timestamps = []
    state.last_webhook_at = {}
    state.last_broker_error_detail = None
    state.watched_symbols = {
        "stock": ["AAPL", "MSFT", "NVDA", "SPY"],
        "forex": ["EUR_USD", "GBP_USD", "USD_JPY"],
        "crypto": ["BTC/USD", "ETH/USD", "SOL/USD"],
    }
    yield


class FakeRiskManager:
    def __init__(self, asset_classes=("stock", "forex", "crypto")):
        self.asset_classes = list(asset_classes)
        self.account_halted = False
        self.trading_halted = {ac: False for ac in self.asset_classes}


def test_discord_webhook_url_is_not_the_real_one_during_tests():
    """Guards against the exact mistake that would make every other test
    in this file post to a real Discord channel."""
    assert not config.DISCORD_ALERT_WEBHOOK_URL


def test_post_to_discord_is_a_noop_without_a_configured_url(monkeypatch):
    import requests
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append((a, k)))
    alerts._post_to_discord("title", "description")
    assert calls == []


def test_github_dispatch_token_is_not_real_during_tests():
    """Guards against the exact mistake that would make every other test
    in this file fire a real repository_dispatch event (and, in the
    worst case, a real self-heal.yml run and PR)."""
    assert not config.GITHUB_DISPATCH_TOKEN


def test_trigger_github_dispatch_is_a_noop_without_a_configured_token(monkeypatch):
    import requests
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append((a, k)))
    alerts._trigger_github_dispatch("bot-halted", {"scope": "account"})
    assert calls == []


class _FakeGithubResponse:
    status_code = 204
    text = ""


def _capture_github_post(monkeypatch, calls):
    """Configures a fake GITHUB_DISPATCH_TOKEN and a requests.post stub
    that records every call instead of hitting the network -- shared by
    the dispatch tests below."""
    import requests
    monkeypatch.setattr(config, "GITHUB_DISPATCH_TOKEN", "fake-token-for-tests")

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return _FakeGithubResponse()

    monkeypatch.setattr(requests, "post", fake_post)


def test_account_halt_dispatches_to_github_with_scope_account(monkeypatch):
    calls = []
    _capture_github_post(monkeypatch, calls)

    rm = FakeRiskManager()
    rm.account_halted = True
    alerts.check_and_alert_bot_halted(rm)

    dispatches = [c for c in calls if c["url"] == alerts.GITHUB_DISPATCH_URL]
    assert len(dispatches) == 1
    assert dispatches[0]["json"]["event_type"] == "bot-halted"
    assert dispatches[0]["json"]["client_payload"]["scope"] == "account"
    assert dispatches[0]["headers"]["Authorization"] == "Bearer fake-token-for-tests"


def test_asset_class_halt_dispatches_to_github_with_that_scope(monkeypatch):
    calls = []
    _capture_github_post(monkeypatch, calls)

    rm = FakeRiskManager()
    rm.trading_halted["forex"] = True
    alerts.check_and_alert_bot_halted(rm)

    dispatches = [c for c in calls if c["url"] == alerts.GITHUB_DISPATCH_URL]
    assert len(dispatches) == 1
    assert dispatches[0]["json"]["event_type"] == "bot-halted"
    assert dispatches[0]["json"]["client_payload"]["scope"] == "forex"


def test_halt_dispatch_does_not_repeat_while_latched(monkeypatch):
    calls = []
    _capture_github_post(monkeypatch, calls)

    rm = FakeRiskManager()
    rm.account_halted = True
    alerts.check_and_alert_bot_halted(rm)
    alerts.check_and_alert_bot_halted(rm)  # still halted -- latch should suppress this one
    alerts.check_and_alert_bot_halted(rm)

    dispatches = [c for c in calls if c["url"] == alerts.GITHUB_DISPATCH_URL]
    assert len(dispatches) == 1


def test_webhook_silence_dispatches_to_github(monkeypatch):
    calls = []
    _capture_github_post(monkeypatch, calls)
    monkeypatch.setattr(alerts, "_is_market_hours", lambda asset_class, now_utc=None: True)
    state.last_webhook_at["EUR_USD"] = time.time() - alerts.WEBHOOK_SILENCE_THRESHOLD_SECONDS - 1

    alerts.check_and_alert_webhook_silence()

    dispatches = [c for c in calls if c["url"] == alerts.GITHUB_DISPATCH_URL]
    assert len(dispatches) == 1
    assert dispatches[0]["json"]["event_type"] == "webhook-silence"
    assert dispatches[0]["json"]["client_payload"]["symbol"] == "EUR_USD"
    assert dispatches[0]["json"]["client_payload"]["asset_class"] == "forex"
    assert "silent_for_hours" in dispatches[0]["json"]["client_payload"]


def test_broker_errors_dispatch_includes_traceback_detail(monkeypatch):
    calls = []
    _capture_github_post(monkeypatch, calls)

    try:
        raise ValueError("simulated broker failure")
    except ValueError:
        import traceback
        for _ in range(alerts.BROKER_ERROR_THRESHOLD):
            alerts.record_broker_error(detail=traceback.format_exc())

    alerts.check_and_alert_broker_errors()

    dispatches = [c for c in calls if c["url"] == alerts.GITHUB_DISPATCH_URL]
    assert len(dispatches) == 1
    assert dispatches[0]["json"]["event_type"] == "broker-errors"
    assert "simulated broker failure" in dispatches[0]["json"]["client_payload"]["traceback"]


# --- _is_market_hours ------------------------------------------------

def test_market_hours_stock_weekday_during_session():
    dt = datetime(2024, 1, 3, 15, 0, tzinfo=timezone.utc)  # Wed 10:00 EST
    assert alerts._is_market_hours("stock", dt) is True


def test_market_hours_stock_weekend():
    dt = datetime(2024, 1, 6, 15, 0, tzinfo=timezone.utc)  # Sat 10:00 EST
    assert alerts._is_market_hours("stock", dt) is False


def test_market_hours_stock_before_open():
    dt = datetime(2024, 1, 3, 12, 0, tzinfo=timezone.utc)  # Wed 07:00 EST
    assert alerts._is_market_hours("stock", dt) is False


def test_market_hours_stock_after_close():
    dt = datetime(2024, 1, 3, 22, 0, tzinfo=timezone.utc)  # Wed 17:00 EST
    assert alerts._is_market_hours("stock", dt) is False


def test_market_hours_crypto_is_always_open():
    # 24/7 market -- includes times every other class is closed.
    saturday_night = datetime(2024, 1, 7, 3, 0, tzinfo=timezone.utc)  # Sat 22:00 EST
    weekday_overnight = datetime(2024, 1, 3, 8, 0, tzinfo=timezone.utc)  # Wed 03:00 EST
    assert alerts._is_market_hours("crypto", saturday_night) is True
    assert alerts._is_market_hours("crypto", weekday_overnight) is True


def test_market_hours_forex_open_overnight_on_weekdays():
    # 03:00 EST Wednesday: NYSE is closed, forex is not -- this is
    # exactly the window the old NYSE-only gate went blind in.
    dt = datetime(2024, 1, 3, 8, 0, tzinfo=timezone.utc)  # Wed 03:00 EST
    assert alerts._is_market_hours("forex", dt) is True


def test_market_hours_forex_sunday_open_boundary():
    before_open = datetime(2024, 1, 7, 21, 30, tzinfo=timezone.utc)  # Sun 16:30 EST
    after_open = datetime(2024, 1, 7, 22, 30, tzinfo=timezone.utc)   # Sun 17:30 EST
    assert alerts._is_market_hours("forex", before_open) is False
    assert alerts._is_market_hours("forex", after_open) is True


def test_market_hours_forex_friday_close_boundary():
    before_close = datetime(2024, 1, 5, 21, 30, tzinfo=timezone.utc)  # Fri 16:30 EST
    after_close = datetime(2024, 1, 5, 22, 30, tzinfo=timezone.utc)   # Fri 17:30 EST
    assert alerts._is_market_hours("forex", before_close) is True
    assert alerts._is_market_hours("forex", after_close) is False


def test_market_hours_forex_closed_saturday():
    dt = datetime(2024, 1, 6, 15, 0, tzinfo=timezone.utc)  # Sat 10:00 EST
    assert alerts._is_market_hours("forex", dt) is False


def test_market_hours_forex_boundaries_are_dst_aware():
    # Same 21:30 UTC clock time as the January (EST, UTC-5) Sunday
    # boundary test above, but in July (EDT, UTC-4) that's already
    # 17:30 ET -- open. A fixed-UTC-offset implementation would get
    # exactly one of these two wrong.
    july_sunday_2130_utc = datetime(2024, 7, 7, 21, 30, tzinfo=timezone.utc)  # Sun 17:30 EDT
    assert alerts._is_market_hours("forex", july_sunday_2130_utc) is True


# --- check_and_alert_bot_halted --------------------------------------

def test_account_halt_alert_latches_then_clears():
    rm = FakeRiskManager()
    alerts.check_and_alert_bot_halted(rm)
    assert state.alerted_account_halted is False  # nothing halted yet

    rm.account_halted = True
    alerts.check_and_alert_bot_halted(rm)
    assert state.alerted_account_halted is True

    alerts.check_and_alert_bot_halted(rm)  # still halted -- latch stays True, no re-alert
    assert state.alerted_account_halted is True

    rm.account_halted = False
    alerts.check_and_alert_bot_halted(rm)
    assert state.alerted_account_halted is False  # cleared, ready to alert again next time


def test_trading_halted_latches_independently_per_asset_class():
    rm = FakeRiskManager()
    rm.trading_halted["stock"] = True
    alerts.check_and_alert_bot_halted(rm)
    assert state.alerted_trading_halted["stock"] is True
    assert state.alerted_trading_halted["forex"] is False
    assert state.alerted_trading_halted["crypto"] is False

    rm.trading_halted["stock"] = False
    alerts.check_and_alert_bot_halted(rm)
    assert state.alerted_trading_halted["stock"] is False


# --- check_and_alert_webhook_silence -----------------------------------

def _all_markets_open(monkeypatch):
    monkeypatch.setattr(alerts, "_is_market_hours", lambda asset_class, now_utc=None: True)


def test_webhook_silence_no_alert_with_no_prior_hit(monkeypatch):
    _all_markets_open(monkeypatch)
    alerts.check_and_alert_webhook_silence()
    assert state.alerted_webhook_silence == {}


def test_webhook_silence_no_alert_within_threshold(monkeypatch):
    _all_markets_open(monkeypatch)
    state.last_webhook_at["AAPL"] = time.time() - 60
    alerts.check_and_alert_webhook_silence()
    assert state.alerted_webhook_silence["AAPL"] is False


def test_webhook_silence_alerts_past_threshold_and_latches(monkeypatch):
    _all_markets_open(monkeypatch)
    state.last_webhook_at["AAPL"] = time.time() - alerts.WEBHOOK_SILENCE_THRESHOLD_SECONDS - 1
    alerts.check_and_alert_webhook_silence()
    assert state.alerted_webhook_silence["AAPL"] is True


def test_webhook_silence_not_checked_outside_market_hours(monkeypatch):
    monkeypatch.setattr(alerts, "_is_market_hours", lambda asset_class, now_utc=None: False)
    state.last_webhook_at["AAPL"] = time.time() - alerts.WEBHOOK_SILENCE_THRESHOLD_SECONDS - 1
    alerts.check_and_alert_webhook_silence()
    assert state.alerted_webhook_silence.get("AAPL") is None  # its class's market never opened -- never checked


def test_webhook_silence_fresh_symbol_does_not_mask_silent_symbol_in_same_class(monkeypatch):
    """The exact audit finding: one symbol's webhook activity used to
    reset a single shared per-asset-class clock and hide a DIFFERENT
    symbol in the SAME class going silent at the same time. NVDA (stock)
    is fresh, AAPL (also stock) is stale -- only AAPL may alert."""
    calls = []
    _capture_github_post(monkeypatch, calls)
    _all_markets_open(monkeypatch)
    now = time.time()
    state.last_webhook_at["NVDA"] = now - 60  # fresh
    state.last_webhook_at["AAPL"] = now - alerts.WEBHOOK_SILENCE_THRESHOLD_SECONDS - 1  # silent

    alerts.check_and_alert_webhook_silence()

    assert state.alerted_webhook_silence["AAPL"] is True
    assert state.alerted_webhook_silence["NVDA"] is False
    dispatches = [c for c in calls if c["url"] == alerts.GITHUB_DISPATCH_URL]
    assert [d["json"]["client_payload"]["symbol"] for d in dispatches] == ["AAPL"]


def test_webhook_silence_fresh_stock_activity_does_not_mask_silent_forex(monkeypatch):
    """Cross-class independence still holds too: a fresh stock symbol
    must not mask a silent forex symbol."""
    calls = []
    _capture_github_post(monkeypatch, calls)
    _all_markets_open(monkeypatch)
    now = time.time()
    state.last_webhook_at["AAPL"] = now - 60  # fresh
    state.last_webhook_at["EUR_USD"] = now - alerts.WEBHOOK_SILENCE_THRESHOLD_SECONDS - 1  # silent

    alerts.check_and_alert_webhook_silence()

    assert state.alerted_webhook_silence["EUR_USD"] is True
    assert state.alerted_webhook_silence["AAPL"] is False
    dispatches = [c for c in calls if c["url"] == alerts.GITHUB_DISPATCH_URL]
    assert [d["json"]["client_payload"]["symbol"] for d in dispatches] == ["EUR_USD"]


def test_webhook_silence_gates_each_class_by_its_own_market_hours(monkeypatch):
    """Crypto must be checked even when stock's and forex's markets are
    closed -- under the old NYSE-only gate it was never checked at all."""
    monkeypatch.setattr(
        alerts, "_is_market_hours",
        lambda asset_class, now_utc=None: asset_class == "crypto",
    )
    stale = time.time() - alerts.WEBHOOK_SILENCE_THRESHOLD_SECONDS - 1
    state.last_webhook_at["AAPL"] = stale
    state.last_webhook_at["BTC/USD"] = stale

    alerts.check_and_alert_webhook_silence()

    assert state.alerted_webhook_silence["BTC/USD"] is True
    assert state.alerted_webhook_silence.get("AAPL") is None  # its market is closed -- not checked


def test_webhook_silence_latch_clears_when_symbol_goes_fresh_again(monkeypatch):
    _all_markets_open(monkeypatch)
    state.last_webhook_at["BTC/USD"] = time.time() - alerts.WEBHOOK_SILENCE_THRESHOLD_SECONDS - 1
    alerts.check_and_alert_webhook_silence()
    assert state.alerted_webhook_silence["BTC/USD"] is True

    state.last_webhook_at["BTC/USD"] = time.time()  # a webhook landed again
    alerts.check_and_alert_webhook_silence()
    assert state.alerted_webhook_silence["BTC/USD"] is False  # ready to alert on the NEXT silence


# --- record_broker_error / check_and_alert_broker_errors ---------------

def test_record_broker_error_prunes_entries_outside_the_window():
    stale = time.time() - alerts.BROKER_ERROR_WINDOW_SECONDS - 10
    state.broker_error_timestamps = [stale]
    alerts.record_broker_error()
    assert len(state.broker_error_timestamps) == 1
    assert state.broker_error_timestamps[0] > stale


def test_broker_errors_below_threshold_does_not_alert():
    for _ in range(alerts.BROKER_ERROR_THRESHOLD - 1):
        alerts.record_broker_error()
    alerts.check_and_alert_broker_errors()
    assert state.alerted_broker_errors is False


def test_broker_errors_at_threshold_alerts_and_latches():
    for _ in range(alerts.BROKER_ERROR_THRESHOLD):
        alerts.record_broker_error()
    alerts.check_and_alert_broker_errors()
    assert state.alerted_broker_errors is True


def test_broker_errors_clears_once_window_ages_out():
    stale = time.time() - alerts.BROKER_ERROR_WINDOW_SECONDS - 10
    state.broker_error_timestamps = [stale] * alerts.BROKER_ERROR_THRESHOLD
    alerts.check_and_alert_broker_errors()
    assert state.alerted_broker_errors is False
    assert state.broker_error_timestamps == []
