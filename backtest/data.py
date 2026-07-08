"""
Historical data fetch for backtesting — wraps each broker's
get_historical_bars() so callers just say "N months back" instead of
computing start/end datetimes themselves.
"""

from datetime import datetime, timedelta, timezone


def fetch_bars_for_backtest(broker, symbol, timeframe, months_back=6, end=None):
    """
    Fetches the last `months_back` months of bars up to `end` (defaults
    to now, UTC) via broker.get_historical_bars(). `months_back` is
    approximated as 30-day blocks — fine for backtesting purposes,
    not calendar-exact.
    """
    end = end or datetime.now(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(days=30 * months_back)
    return broker.get_historical_bars(symbol, timeframe=timeframe, start=start, end=end)
