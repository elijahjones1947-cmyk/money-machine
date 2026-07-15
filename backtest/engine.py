"""
Bar-by-bar backtest engine for the Higher High Breakout strategy.

Walks the historical bars in order, using signals computed fresh at
each step (backtest.strategy.compute_signals — no lookahead, since a
signal at bar i only ever uses bars[0..i]), and simulates fills the way
TradingView's DEFAULT strategy behavior works for this script (it
never sets process_orders_on_close or calc_on_every_tick):

  - Entries and the momentum exit are signals evaluated on a bar's
    CLOSE, but FILL at the NEXT bar's OPEN.
  - Take-profit/stop-loss are resting limit/stop orders once a position
    is open, so they can fill INTRABAR — checked against each
    subsequent bar's high/low, not just its close.
  - If both TP and SL are touched within the same bar, we conservatively
    assume the stop-loss triggered first (OHLC alone can't tell you the
    intrabar path — this is the standard conservative tie-break used by
    most bar-resolution backtesters).
  - Mirrors the live Pine script's EXITS section: once price covers 50%
    of the distance from entry to the take-profit target, a trailing
    stop activates and follows the position's peak price by half the
    stop-loss distance -- both derived from take_profit_pct/
    stop_loss_pct, not separate params. The fixed take-profit limit
    stays live throughout (a straight run to full TP still fills there,
    trailing or not); before activation the fixed stop-loss protects
    the position same as always.

This is a bar-resolution approximation, not a tick-by-tick replay (same
as almost every programmatic backtest that isn't literally replaying
trade-by-trade data) — good enough to compare strategy performance
across regimes, not a promise of matching TradingView's own backtest
report bar-for-bar.
"""

from backtest.strategy import compute_signals, DEFAULT_PARAMS


def run_backtest(bars, params=None, initial_capital=10000.0, qty_pct_of_equity=100.0):
    """
    bars: OHLCV list, oldest first — a long historical stretch from
    broker.get_historical_bars(), NOT the ~100-bar live regime window.

    Returns a list of closed trade dicts, in chronological order:
        entry_index, entry_time, entry_price,
        exit_index, exit_time, exit_price,
        exit_reason: "take_profit" | "stop_loss" | "trailing_stop" |
                     "momentum_exit" | "end_of_data"
        pnl_pct, pnl_abs, hold_bars

    Position sizing mirrors the script's default_qty_value=100
    (100% of a fixed initial_capital per trade, non-compounding — this
    is a strategy quality check, not an equity-curve simulator).
    """
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)

    signals = compute_signals(bars, p)
    n = len(bars)

    trades = []
    position = None
    pending_entry_index = None  # buy_condition seen on bar i -> fill at bar i+1's open

    for i in range(n):
        bar = bars[i]

        # Fill a pending entry at THIS bar's open (signal raised on the
        # previous bar's close).
        if position is None and pending_entry_index == i:
            entry_price = bar["open"]
            equity_at_risk = initial_capital * (qty_pct_of_equity / 100.0)
            position = {
                "entry_index": i,
                "entry_time": bar["time"],
                "entry_price": entry_price,
                "take_profit_price": entry_price * (1 + p["take_profit_pct"] / 100),
                "stop_loss_price": entry_price * (1 - p["stop_loss_pct"] / 100),
                "equity_at_risk": equity_at_risk,
                # Trailing stop: activates once price reaches halfway to
                # TP, then follows the peak price by half the SL
                # distance -- see the module docstring.
                "trail_activate_price": entry_price * (1 + (p["take_profit_pct"] * 0.5) / 100),
                "trail_offset": entry_price * (p["stop_loss_pct"] * 0.5) / 100,
                "trailing_active": False,
                "peak_price": entry_price,
            }
            pending_entry_index = None

        # Manage an open position: TP/SL can fill intrabar; momentum
        # exit is a close-triggered market order.
        if position is not None:
            position["peak_price"] = max(position["peak_price"], bar["high"])
            if not position["trailing_active"] and bar["high"] >= position["trail_activate_price"]:
                position["trailing_active"] = True

            if position["trailing_active"]:
                # Ratchets up on its own since peak_price only ever
                # grows -- no separate high-water tracking needed.
                effective_stop_price = position["peak_price"] - position["trail_offset"]
            else:
                effective_stop_price = position["stop_loss_price"]

            hit_tp = bar["high"] >= position["take_profit_price"]
            hit_sl = bar["low"] <= effective_stop_price

            exit_price = None
            exit_reason = None
            if hit_sl:
                # Covers both the SL-only case and the same-bar TP+SL
                # ambiguity, which we conservatively resolve as SL-first.
                exit_price = effective_stop_price
                exit_reason = "trailing_stop" if position["trailing_active"] else "stop_loss"
            elif hit_tp:
                exit_price = position["take_profit_price"]
                exit_reason = "take_profit"

            if exit_price is None and signals[i]["sell_signal"] and i + 1 < n:
                # Fills at the NEXT bar's open, matching Pine's default
                # order-fill timing for a market order raised on close.
                exit_price = bars[i + 1]["open"]
                exit_reason = "momentum_exit"

            if exit_price is not None:
                trades.append(_close_trade(position, i, bar["time"], exit_price, exit_reason))
                position = None

        # Raise a new entry signal off this bar's close (only if flat
        # and nothing already pending), to fill next bar's open.
        if position is None and pending_entry_index is None and signals[i]["buy_condition"]:
            if i + 1 < n:
                pending_entry_index = i + 1

    # Still holding at the end of the data: mark-to-market at the final
    # close so metrics aren't skewed by a trade that never got the
    # chance to hit TP/SL/momentum-exit.
    if position is not None:
        last = bars[-1]
        trades.append(_close_trade(position, n - 1, last["time"], last["close"], "end_of_data"))

    return trades


def _close_trade(position, exit_index, exit_time, exit_price, exit_reason):
    entry_price = position["entry_price"]
    pnl_pct = ((exit_price - entry_price) / entry_price) * 100
    pnl_abs = position["equity_at_risk"] * (pnl_pct / 100)
    return {
        "entry_index": position["entry_index"],
        "entry_time": position["entry_time"],
        "entry_price": entry_price,
        "exit_index": exit_index,
        "exit_time": exit_time,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "pnl_pct": pnl_pct,
        "pnl_abs": pnl_abs,
        "hold_bars": exit_index - position["entry_index"],
    }
