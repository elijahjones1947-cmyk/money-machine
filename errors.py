"""
Standardized error types used across all brokers.

Each broker (Alpaca, OANDA) catches its own native exceptions and
re-raises them as one of these, so the rest of the app (strategies,
risk manager, webhook route) only ever has to handle ONE consistent
set of error types regardless of which broker raised them.
"""


class BrokerError(Exception):
    """Base class for all broker-related errors."""
    pass


class InsufficientFundsError(BrokerError):
    """Not enough cash/margin to place the trade."""
    pass


class MarketClosedError(BrokerError):
    """The market for this symbol is currently closed."""
    pass


class InvalidSymbolError(BrokerError):
    """The symbol/instrument doesn't exist or isn't tradable."""
    pass


class BrokerConnectionError(BrokerError):
    """Catch-all for API/network errors that don't fit the above categories."""
    pass
