import requests

from brokers.base import BrokerInterface
from errors import (
    InsufficientFundsError,
    MarketClosedError,
    InvalidSymbolError,
    BrokerConnectionError,
)


class OandaBroker(BrokerInterface):
    """
    Talks to OANDA's v20 REST API directly via requests.
    Forex is sized in units of currency (not shares/lots) — e.g.
    1000 units of EUR_USD, not "1 lot". Instruments use OANDA's
    underscore format, e.g. 'EUR_USD', not 'EUR/USD'.
    """

    def __init__(self, api_key, account_id, base_url):
        self.account_id = account_id
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": "Bearer {}".format(api_key),
            "Content-Type": "application/json",
        })

    def get_price(self, symbol):
        url = "{}/v3/accounts/{}/pricing".format(self.base_url, self.account_id)
        try:
            resp = self.session.get(url, params={"instruments": symbol}, timeout=10)
            data = resp.json()
            if resp.status_code != 200:
                self._translate_error(resp.status_code, data, symbol)
            prices = data.get("prices", [])
            if not prices:
                raise InvalidSymbolError("OANDA: no pricing returned for {}".format(symbol))
            # use the mid of bid/ask as the reference price
            bid = float(prices[0]["bids"][0]["price"])
            ask = float(prices[0]["asks"][0]["price"])
            return (bid + ask) / 2
        except requests.RequestException as e:
            raise BrokerConnectionError("OANDA connection error: {}".format(e))

    def place_order(self, symbol, side, size, order_type="market"):
        units = int(size) if side == "buy" else -int(size)
        url = "{}/v3/accounts/{}/orders".format(self.base_url, self.account_id)
        order_payload = {
            "order": {
                "instrument": symbol,
                "units": str(units),
                "type": "MARKET",
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
            }
        }
        try:
            resp = self.session.post(url, json=order_payload, timeout=10)
            data = resp.json()
            if resp.status_code not in (200, 201):
                self._translate_error(resp.status_code, data, symbol)
            if "orderCancelTransaction" in data:
                # order was rejected/cancelled by OANDA even with a 2xx response
                reason = data["orderCancelTransaction"].get("reason", "UNKNOWN")
                self._translate_error(400, {"errorCode": reason}, symbol)
            return data
        except requests.RequestException as e:
            raise BrokerConnectionError("OANDA connection error: {}".format(e))

    def get_positions(self):
        url = "{}/v3/accounts/{}/openPositions".format(self.base_url, self.account_id)
        try:
            resp = self.session.get(url, timeout=10)
            data = resp.json()
            if resp.status_code != 200:
                self._translate_error(resp.status_code, data, None)
            return data.get("positions", [])
        except requests.RequestException as e:
            raise BrokerConnectionError("OANDA connection error: {}".format(e))

    def get_account_info(self):
        url = "{}/v3/accounts/{}/summary".format(self.base_url, self.account_id)
        try:
            resp = self.session.get(url, timeout=10)
            data = resp.json()
            if resp.status_code != 200:
                self._translate_error(resp.status_code, data, None)
            acct = data["account"]
            equity = float(acct["NAV"])
            return {
                "equity": equity,
                "buying_power": float(acct["marginAvailable"]),
                "last_equity": equity - float(acct.get("unrealizedPL", 0)),
            }
        except requests.RequestException as e:
            raise BrokerConnectionError("OANDA connection error: {}".format(e))

    def cancel_order(self, order_id):
        url = "{}/v3/accounts/{}/orders/{}/cancel".format(self.base_url, self.account_id, order_id)
        try:
            resp = self.session.put(url, timeout=10)
            data = resp.json()
            if resp.status_code != 200:
                self._translate_error(resp.status_code, data, None)
            return data
        except requests.RequestException as e:
            raise BrokerConnectionError("OANDA connection error: {}".format(e))

    def _translate_error(self, status_code, data, symbol):
        """Map OANDA's error response into our standard error types."""
        error_code = (data or {}).get("errorCode", "") or ""
        error_message = (data or {}).get("errorMessage", "") or ""
        combined = "{} {}".format(error_code, error_message).upper()

        if "INSUFFICIENT_MARGIN" in combined or "INSUFFICIENT_AUTHORIZATION" in combined:
            raise InsufficientFundsError("OANDA: insufficient margin for {}".format(symbol))
        if "MARKET_HALTED" in combined or "MARKET_CLOSED" in combined:
            raise MarketClosedError("OANDA: market closed for {}".format(symbol))
        if "INSTRUMENT" in combined and ("INVALID" in combined or "NOT_FOUND" in combined):
            raise InvalidSymbolError("OANDA: invalid instrument {}".format(symbol))
        raise BrokerConnectionError(
            "OANDA error ({}): {}".format(status_code, error_message or error_code or "unknown error")
        )
