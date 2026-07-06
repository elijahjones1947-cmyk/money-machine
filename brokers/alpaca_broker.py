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
    Wraps the alpaca-trade-api SDK. Handles BOTH stocks and crypto through
    the same account/credentials (that's how Alpaca actually works — one
    account, one equity balance, covering both asset classes).

    Stocks: whole-share quantities, 'day' time_in_force.
    Crypto: fractional quantities allowed, symbol format 'BTC/USD' (a
    slash is what tells us a symbol is crypto), and Alpaca only accepts
    'gtc' or 'ioc' time_in_force for crypto orders (NOT 'day').

    NOTE: we deliberately catch the broad `Exception` (not just Alpaca's
    own APIError) around every call. The underlying SDK can raise plain
    requests.HTTPError on network/auth failures that never get wrapped
    into APIError, so narrower catches let those crash the app instead
    of failing gracefully as a BrokerConnectionError.
    """

    def __init__(self, api_key, secret_key, base_url):
        self.client = tradeapi.REST(api_key, secret_key, base_url, api_version="v2")

    @staticmethod
    def _is_crypto(symbol):
        return "/" in symbol

    def get_price(self, symbol):
        try:
            if self._is_crypto(symbol):
                trades = self.client.get_latest_crypto_trades([symbol])
                return float(trades[symbol].price)
            return float(self.client.get_latest_trade(symbol).price)
        except Exception as e:
            self._translate_error(e, symbol)

    def place_order(self, symbol, side, size, order_type="market"):
        try:
            is_crypto = self._is_crypto(symbol)
            qty = round(float(size), 6) if is_crypto else int(size)
            if qty <= 0:
                raise InvalidSymbolError(
                    "Alpaca: computed quantity <= 0 for {}".format(symbol)
                )
            return self.client.submit_order(
                symbol=symbol,
                qty=qty,
                side=side,
                type=order_type,
                time_in_force="gtc" if is_crypto else "day",
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
