"""
Tests for AlpacaBroker.place_order's quantity handling -- specifically
the crypto floor-not-round fix (see the comment in place_order() for the
full diagnosis): a real manual-close attempt on a held ETH/USD quantity
of 1.5661149 got rejected by Alpaca as "insufficient funds" because
round(1.5661149, 6) == 1.566115, which is already ~1e-7 MORE than what
was actually held -- Alpaca (correctly) won't let a sell request for
fractionally more than the account holds.

No real network calls here -- constructing an AlpacaBroker only builds
the SDK's REST client object, same reasoning as test_oanda_broker.py for
why this is safe/cheap to test directly. place_order()'s own broker call
is monkeypatched to capture what was actually submitted instead of
hitting Alpaca.
"""

from brokers.alpaca_broker import AlpacaBroker


def _make_broker():
    return AlpacaBroker(api_key="test-key", secret_key="test-secret", base_url="https://paper-api.alpaca.markets")


def test_place_order_floors_crypto_qty_never_rounds_above_held_amount():
    broker = _make_broker()
    calls = []
    broker.client.submit_order = lambda **kwargs: calls.append(kwargs) or {"id": "fake"}

    # The exact real-world value that triggered the bug: round(1.5661149, 6)
    # == 1.566115, which is greater than 1.5661149 -- flooring must not be.
    broker.place_order("ETH/USD", "sell", 1.5661149)

    submitted_qty = calls[0]["qty"]
    assert submitted_qty <= 1.5661149
    assert submitted_qty == 1.566114


def test_place_order_crypto_qty_still_rounds_down_cleanly_for_exact_values():
    broker = _make_broker()
    calls = []
    broker.client.submit_order = lambda **kwargs: calls.append(kwargs) or {"id": "fake"}

    broker.place_order("BTC/USD", "buy", 0.235)

    assert calls[0]["qty"] == 0.235


def test_place_order_stock_qty_is_a_whole_share_count():
    broker = _make_broker()
    calls = []
    broker.client.submit_order = lambda **kwargs: calls.append(kwargs) or {"id": "fake"}

    broker.place_order("AAPL", "sell", 31.0)

    assert calls[0]["qty"] == 31
    assert isinstance(calls[0]["qty"], int)


def test_place_order_uses_gtc_for_crypto_and_day_for_stock():
    broker = _make_broker()
    calls = []
    broker.client.submit_order = lambda **kwargs: calls.append(kwargs) or {"id": "fake"}

    broker.place_order("BTC/USD", "sell", 0.1)
    broker.place_order("AAPL", "sell", 1)

    assert calls[0]["time_in_force"] == "gtc"
    assert calls[1]["time_in_force"] == "day"
