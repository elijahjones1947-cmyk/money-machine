from datetime import datetime, timedelta

import requests

from brokers.base import BrokerInterface
from errors import (
    InsufficientFundsError,
    MarketClosedError,
    InvalidSymbolError,
    BrokerConnectionError,
)


_GRANULARITY_MAP = {
    "1m": "M1",
    "5m": "M5",
    "15m": "M15",
    "1h": "H1",
    "4h": "H4",
    "1d": "D",
}

# OANDA caps candle requests at 5000 per call regardless of granularity.
_MAX_CANDLES_PER_REQUEST = 5000


def _parse_oanda_time(s):
    """
    OANDA candle timestamps look like '2024-05-01T12:00:00.123456789Z'
    (nanosecond precision, trailing Z). Python's datetime only handles
    microseconds, so this truncates the fractional part before parsing.
    Returned as a naive UTC datetime, consistent with the rest of the
    codebase (no tzinfo attached anywhere else either).
    """
    s = s.rstrip("Z")
    if "." in s:
        base, frac = s.split(".")
        s = "{}.{}".format(base, frac[:6])
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f")
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")


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

    def get_ohlcv(self, symbol, timeframe="1h", limit=100):
        granularity = _GRANULARITY_MAP.get(timeframe)
        if granularity is None:
            raise ValueError("Unsupported timeframe: {}".format(timeframe))
        url = "{}/v3/instruments/{}/candles".format(self.base_url, symbol)
        params = {"granularity": granularity, "count": limit, "price": "M"}  # M = midpoint
        try:
            resp = self.session.get(url, params=params, timeout=10)
            data = resp.json()
            if resp.status_code != 200:
                self._translate_error(resp.status_code, data, symbol)
            bars = []
            for c in data.get("candles", []):
                if not c.get("complete", True):
                    continue  # skip the still-forming current candle
                mid = c["mid"]
                bars.append({
                    "time": c["time"],
                    "open": float(mid["o"]),
                    "high": float(mid["h"]),
                    "low": float(mid["l"]),
                    "close": float(mid["c"]),
                    "volume": float(c.get("volume", 0)),
                })
            return bars
        except requests.RequestException as e:
            raise BrokerConnectionError("OANDA connection error: {}".format(e))

    def get_historical_bars(self, symbol, timeframe="1h", start=None, end=None):
        """
        Pulls OHLCV across [start, end) for backtesting, paginating in
        _MAX_CANDLES_PER_REQUEST-candle pages via OANDA's from/to params
        (months of 1h/1m history routinely exceeds a single request's cap).

        Note this returns 'time' as a parsed datetime (unlike get_ohlcv,
        which leaves OANDA's raw time string as-is) — the backtest engine
        needs real datetimes to compare/sort against Alpaca's bars.
        """
        if start is None or end is None:
            raise ValueError("get_historical_bars requires both start and end")
        granularity = _GRANULARITY_MAP.get(timeframe)
        if granularity is None:
            raise ValueError("Unsupported timeframe: {}".format(timeframe))

        url = "{}/v3/instruments/{}/candles".format(self.base_url, symbol)
        all_bars = {}
        cursor = start

        while cursor < end:
            # OANDA rejects requests that mix 'count' with BOTH 'from'
            # and 'to' ("'count' cannot be specified when 'to' and
            # 'from' parameters are set") — so we page forward using
            # 'from' + 'count' only, and cut candles off at `end`
            # ourselves once a page runs past it.
            params = {
                "granularity": granularity,
                "price": "M",
                "from": cursor.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
                "count": _MAX_CANDLES_PER_REQUEST,
            }
            try:
                resp = self.session.get(url, params=params, timeout=30)
                data = resp.json()
                if resp.status_code != 200:
                    self._translate_error(resp.status_code, data, symbol)
            except requests.RequestException as e:
                raise BrokerConnectionError("OANDA connection error: {}".format(e))

            candles = data.get("candles", [])
            if not candles:
                break

            last_time_str = None
            reached_end = False
            for c in candles:
                candle_time = _parse_oanda_time(c["time"])
                if candle_time >= end:
                    reached_end = True
                    break
                if not c.get("complete", True):
                    continue
                mid = c["mid"]
                all_bars[c["time"]] = {
                    "time": candle_time,
                    "open": float(mid["o"]),
                    "high": float(mid["h"]),
                    "low": float(mid["l"]),
                    "close": float(mid["c"]),
                    "volume": float(c.get("volume", 0)),
                }
                last_time_str = c["time"]

            if reached_end:
                break

            if last_time_str is None:
                break

            last_dt = _parse_oanda_time(last_time_str)
            if last_dt <= cursor:
                break  # safety valve: no forward progress, stop
            cursor = last_dt + timedelta(seconds=1)

            if len(candles) < _MAX_CANDLES_PER_REQUEST:
                break  # short page means we've reached `to`

        return [all_bars[k] for k in sorted(all_bars.keys())]

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
