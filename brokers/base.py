"""
BrokerInterface: the contract every broker must implement.

Your strategy/risk-manager code only ever talks to this interface —
never to AlpacaBroker or OandaBroker directly by name. That's what
lets the same webhook route and risk logic work for both stocks and
forex without caring which broker is underneath.
"""

from abc import ABC, abstractmethod


class BrokerInterface(ABC):

    @abstractmethod
    def get_price(self, symbol):
        """Return the current price (float) for a symbol/instrument."""
        raise NotImplementedError

    @abstractmethod
    def place_order(self, symbol, side, size, order_type="market"):
        """
        Place an order.
        side = 'buy' or 'sell'
        size = shares (stocks) or units (forex)
        Returns the broker's order confirmation object/dict.
        Raises one of the errors in errors.py on failure.
        """
        raise NotImplementedError

    @abstractmethod
    def get_positions(self):
        """Return a list of current open positions."""
        raise NotImplementedError

    @abstractmethod
    def get_account_info(self):
        """
        Return a dict with at least:
        { 'equity': float, 'buying_power': float, 'last_equity': float }
        """
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id):
        raise NotImplementedError

    @abstractmethod
    def get_ohlcv(self, symbol, timeframe="1h", limit=100):
        """
        Return historical OHLCV bars, oldest first, as a list of dicts:
        [{"time": datetime, "open": float, "high": float, "low": float,
          "close": float, "volume": float}, ...]

        `timeframe` is a normalized string ('1m', '15m', '1h', '4h', '1d')
        that each broker maps to its own API's format internally — callers
        never need to know the broker-specific granularity syntax.

        Built for the regime classifier's needs: a short, recent window
        (default limit=100). For long historical stretches, use
        get_historical_bars() instead.
        """
        raise NotImplementedError

    @abstractmethod
    def get_historical_bars(self, symbol, timeframe, start, end):
        """
        Return OHLCV bars for the half-open range [start, end), oldest
        first, same dict shape as get_ohlcv (with 'time' as a real
        datetime, not a string, on both brokers). `start`/`end` are
        naive UTC datetimes.

        Unlike get_ohlcv (last N bars, for live regime checks), this is
        built for backtesting: it paginates internally so callers can
        request months of history in a single call without worrying
        about each broker API's own per-request bar cap.
        """
        raise NotImplementedError
