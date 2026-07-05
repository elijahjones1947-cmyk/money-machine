import alpaca_trade_api as tradeapi

from brokers.base import BrokerInterface
from errors import (
    InsufficientFundsError,
    MarketClosedError,
    InvalidSymbolError,
    BrokerConnectionError,
)


class AlpacaBroker(BrokerInterface):
    """
    Wraps the alpaca-trade-api SDK. Stocks are sized in whole shares.

    NOTE: we deliberately catch the broad `Exception` (not just Alpaca's
    own APIError) around every call. The underlying SDK can raise plain
    requests.HTTPError on network/auth failures that never get wrapped
    into APIError, so narrower catches let those crash the app instead
    of failing gracefully as a BrokerConnectionError.
    """

    def __init__(self, api_key, secret_key, base_url):
        self.client = tradeapi.REST(api_key, secret_key, base_url, api_version="v2")

    def get_price(self, symbol):
        try:
            return float(self.client.get_latest_trade(symbol).price)
        except Exception as e:
            self._translate_error(e, symbol)

    def place_order(self, symbol, side, size, order_type="market"):
        try:
            qty = int(size)
            if qty < 1:
                raise InvalidSymbolError(
                    "Alpaca: computed quantity < 1 share for {}".format(symbol)
                )
            return self.client.submit_order(
                symbol=symbol,
                qty=qty,
                side=side,
                type=order_type,
                time_in_force="day",
            )
        except InvalidSymbolError:
            raise
        except Exception as e:
            self._translate_error(e, symbol)

    def get_positions(self):
        try:
            return self.client.list_positions()
        except Exception as e:
            self._translate_error(e, None)

    def get_account_info(self):
        try:
            acct = self.client.get_account()
            return {
                "equity": float(acct.equity),
                "buying_power": float(acct.buying_power),
                "last_equity": float(acct.last_equity),
            }
        except Exception as e:
            self._translate_error(e, None)

    def cancel_order(self, order_id):
        try:
            return self.client.cancel_order(order_id)
        except Exception as e:
            self._translate_error(e, None)

    def _translate_error(self, e, symbol):
        """Map any Alpaca/network failure into our standard error types."""
        msg = str(e).lower()
        if "insufficient" in msg or "buying power" in msg:
            raise InsufficientFundsError("Alpaca: insufficient funds for {}".format(symbol))
        if "market is closed" in msg or "market closed" in msg:
            raise MarketClosedError("Alpaca: market closed for {}".format(symbol))
        if "not found" in msg or "invalid symbol" in msg or "unknown symbol" in msg:
            raise InvalidSymbolError("Alpaca: invalid symbol {}".format(symbol))
        raise BrokerConnectionError("Alpaca error: {}".format(e))
