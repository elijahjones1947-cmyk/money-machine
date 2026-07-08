from datetime import timedelta

import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import TimeFrame, TimeFrameUnit

from brokers.base import BrokerInterface
from errors import (
    InsufficientFundsError,
    MarketClosedError,
    InvalidSymbolError,
    BrokerConnectionError,
)


_TIMEFRAME_MAP = {
    "1m": TimeFrame.Minute,
    "5m": TimeFrame(5, TimeFrameUnit.Minute),
    "15m": TimeFrame(15, TimeFrameUnit.Minute),
    "1h": TimeFrame.Hour,
    "4h": TimeFrame(4, TimeFrameUnit.Hour),
    "1d": TimeFrame.Day,
}

# Chunk size for get_historical_bars. Requesting month-sized windows
# (rather than one request for the whole range) keeps each call well
# under Alpaca's per-request bar cap regardless of installed SDK
# version, instead of depending on the SDK's own pagination behavior.
_HISTORICAL_CHUNK_DAYS = 30


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

    def get_ohlcv(self, symbol, timeframe="1h", limit=100):
        tf = _TIMEFRAME_MAP.get(timeframe)
        if tf is None:
            raise ValueError("Unsupported timeframe: {}".format(timeframe))
        try:
            is_crypto = "/" in symbol
            if is_crypto:
                bars = self.client.get_crypto_bars(symbol, tf, limit=limit)
            else:
                bars = self.client.get_bars(symbol, tf, limit=limit, feed="iex")
                # feed="iex": free/basic Alpaca market data plans don't
                # permit querying recent SIP data (the default feed) —
                # IEX is included on every plan and has no such recency gate.
            df = bars.df
            return [
                {
                    "time": idx.to_pydatetime(),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                }
                for idx, row in df.iterrows()
            ]
        except Exception as e:
            self._translate_error(e, symbol)

    def get_historical_bars(self, symbol, timeframe="1h", start=None, end=None):
        """
        Pulls OHLCV across [start, end) for backtesting, one
        _HISTORICAL_CHUNK_DAYS-day chunk at a time, concatenated and
        deduped by timestamp. Same dict shape as get_ohlcv().
        """
        if start is None or end is None:
            raise ValueError("get_historical_bars requires both start and end")
        tf = _TIMEFRAME_MAP.get(timeframe)
        if tf is None:
            raise ValueError("Unsupported timeframe: {}".format(timeframe))

        is_crypto = self._is_crypto(symbol)
        all_bars = {}  # keyed by ISO time string, dedupes overlapping chunk edges

        chunk_start = start
        while chunk_start < end:
            chunk_end = min(chunk_start + timedelta(days=_HISTORICAL_CHUNK_DAYS), end)
            try:
                # Alpaca's API requires RFC3339 (a timezone designator is
                # mandatory) — plain .isoformat() on our naive UTC datetimes
                # omits it and gets rejected, so we append 'Z' explicitly.
                start_str = chunk_start.isoformat() + "Z"
                end_str = chunk_end.isoformat() + "Z"
                if is_crypto:
                    bars = self.client.get_crypto_bars(
                        symbol,
                        tf,
                        start=start_str,
                        end=end_str,
                        limit=10000,
                    )
                else:
                    bars = self.client.get_bars(
                        symbol,
                        tf,
                        start=start_str,
                        end=end_str,
                        limit=10000,
                        feed="iex",  # see get_ohlcv() for why
                    )
                df = bars.df
                for idx, row in df.iterrows():
                    t = idx.to_pydatetime()
                    all_bars[t.isoformat()] = {
                        "time": t,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                    }
            except Exception as e:
                self._translate_error(e, symbol)
            chunk_start = chunk_end

        return [all_bars[k] for k in sorted(all_bars.keys())]

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
