"""
Generates human-readable rationale for executed trades, from the exact
indicator/signal data server.py already computes (or can cheaply
compute) at execution time -- see strategy_knowledge.py for what each
rule MEANS, this module is what turns computed values into a sentence
about a SPECIFIC trade.

Deliberately template-based (plain Python string formatting), not
LLM-generated: fast, free, deterministic, and doesn't depend on
ANTHROPIC_API_KEY being configured (Hermes already 503s without it --
trade logging must never be able to fail or stall for the same reason).
Hermes can narrate these conversationally when asked, same as it
narrates get_daily_summary's numbers -- this module is the source of
truth for wording, not Hermes.

Entry explanations (explain_entry) are generated from
server._compute_current_signal()'s output -- the same live indicator
computation _sanity_check_signal already does for webhook signals,
reused rather than recomputed.

Exit explanations (explain_exit) are a best-effort INFERENCE, not an
authoritative report -- see that function's docstring for exactly why,
and its limits.
"""


def _fmt_price(price, asset_class):
    if asset_class == "forex":
        return "{:.5f}".format(price)
    if asset_class == "crypto":
        return "{:.4f}".format(price)
    return "{:.2f}".format(price)


_PATTERN_CLAUSE_TEXT = {
    ("doji", "neutral"): "a doji candle at entry signals some indecision worth noting",
    ("pin_bar", "bullish"): "a bullish pin bar (hammer) shows rejection of lower prices",
    ("pin_bar", "bearish"): "a bearish pin bar (shooting star) shows rejection of higher prices",
    ("engulfing", "bullish"): "a bullish engulfing candle confirms strong buying pressure",
    ("engulfing", "bearish"): "a bearish engulfing candle shows strong selling pressure",
}


def _pattern_clauses(detected_patterns):
    """detected_patterns: patterns.analyze_patterns()'s return value (or
    None/empty if pattern detection wasn't run or found nothing).
    Returns a list of clause fragments to fold into an explanation --
    empty if there's nothing to add."""
    if not detected_patterns:
        return []
    clauses = []
    for name, direction in (detected_patterns.get("candlestick_patterns") or {}).items():
        text = _PATTERN_CLAUSE_TEXT.get((name, direction))
        if text:
            clauses.append(text)
    fib = detected_patterns.get("fibonacci_level")
    if fib:
        clauses.append(
            "price is sitting near the {} Fibonacci retracement level ({})".format(
                fib["name"], _round_or_none(fib["price"])
            )
        )
    return clauses


def explain_entry(action, symbol, asset_class, price, signal, params, is_manual=False, is_short=False,
                   detected_patterns=None):
    """
    action: 'buy' or 'sell' -- 'sell' here means a SHORT entry (forex
        only; stock/crypto never short in this app), not a closing sell.
    signal: server._compute_current_signal()'s return value (a dict from
        backtest.strategy.compute_signals(), or None if it couldn't be
        computed -- e.g. a broker error, or not enough bar history yet).
    params: the strategy params dict this signal was computed with
        (db.get_symbol_strategy_assignment(symbol)['params'], falling
        back to backtest.strategy.DEFAULT_PARAMS if the symbol has no
        assignment yet).
    is_manual: True for /api/manual_trade-originated entries -- these
        don't go through _sanity_check_signal at all today, so `signal`
        will usually be freshly computed just for this explanation
        rather than reused from a gating check.
    is_short: True for a forex short entry -- see the module docstring;
        backtest.strategy.compute_signals() only models the LONG entry
        conditions (that's what the live Pine script's alert() calls
        this repo can observe actually gate on), so a short entry has no
        rule-based rationale to report, only a factual indicator
        snapshot.
    detected_patterns: patterns.analyze_patterns()'s return value, or
        None if pattern detection wasn't run/failed -- folded in as
        SUPPORTING context alongside the breakout/EMA/RSI rationale,
        never as a replacement for it (candlestick/Fibonacci patterns
        aren't part of the strategy's own entry conditions, so they
        never appear as a "did NOT confirm" clause the way the real
        gating conditions do).
    """
    prefix = "Entered long" if action == "buy" and not is_short else "Entered short"
    origin = " (manual)" if is_manual else ""
    price_str = _fmt_price(price, asset_class)
    pattern_clauses = _pattern_clauses(detected_patterns)

    if is_short:
        if signal is None:
            text = (
                "{}{}: at {} -- no indicator data available at execution time, and short-entry "
                "conditions aren't modeled by the Python strategy port (long-only breakout logic) "
                "regardless.".format(prefix, origin, price_str)
            )
        else:
            text = (
                "{}{}: at {}. EMA{}={}, EMA{}={}, RSI={} at execution time -- short-entry conditions "
                "aren't modeled by the Python strategy port (long-only breakout logic), so this is a "
                "factual snapshot, not a rule-based rationale.".format(
                    prefix, origin, price_str,
                    params.get("ema_fast_length"), _round_or_none(signal.get("ema_fast")),
                    params.get("ema_slow_length"), _round_or_none(signal.get("ema_slow")),
                    _round_or_none(signal.get("rsi")),
                )
            )
        if pattern_clauses:
            text += " Also: {}.".format(", ".join(pattern_clauses))
        return text

    if signal is None:
        text = "{}{}: at {} -- indicator data unavailable at execution time, no rationale generated.".format(
            prefix, origin, price_str
        )
        if pattern_clauses:
            text += " Also: {}.".format(", ".join(pattern_clauses))
        return text

    clauses = []

    lookback = params.get("lookback")
    buffer_pct = params.get("breakout_buffer_pct")
    if signal.get("higher_high_breakout"):
        clauses.append(
            "price broke above the {}-bar high by the {}% buffer (level {})".format(
                lookback, buffer_pct, _round_or_none(signal.get("breakout_price"))
            )
        )
    elif signal.get("breakout_price") is not None:
        clauses.append(
            "price did NOT confirm a break of the {}-bar high + buffer (level {}, still below it)".format(
                lookback, _round_or_none(signal.get("breakout_price"))
            )
        )

    ema_fast_len = params.get("ema_fast_length")
    ema_slow_len = params.get("ema_slow_length")
    if signal.get("trend_bullish"):
        clauses.append(
            "EMA{} ({}) > EMA{} ({}) confirms uptrend".format(
                ema_fast_len, _round_or_none(signal.get("ema_fast")),
                ema_slow_len, _round_or_none(signal.get("ema_slow")),
            )
        )
    elif signal.get("ema_fast") is not None:
        clauses.append(
            "EMA{} ({}) did NOT confirm an uptrend against EMA{} ({})".format(
                ema_fast_len, _round_or_none(signal.get("ema_fast")),
                ema_slow_len, _round_or_none(signal.get("ema_slow")),
            )
        )

    if signal.get("higher_low"):
        clauses.append("a higher low confirms the whole range is shifting up, not just one spike")
    elif signal.get("recent_low") is not None:
        clauses.append("the low did NOT confirm a rising range (not a higher low)")

    if params.get("use_rsi_filter"):
        rsi_min = params.get("rsi_min")
        if signal.get("rsi_ok"):
            clauses.append(
                "RSI {} supports momentum (>= {} threshold)".format(_round_or_none(signal.get("rsi")), rsi_min)
            )
        elif signal.get("rsi") is not None:
            clauses.append(
                "RSI {} did NOT clear the {} momentum threshold".format(_round_or_none(signal.get("rsi")), rsi_min)
            )

    if not clauses and not pattern_clauses:
        return "{}{}: at {} -- indicators computed but no rule conditions were evaluable yet.".format(
            prefix, origin, price_str
        )

    return "{}{}: {}.".format(prefix, origin, ", ".join(clauses + pattern_clauses))


def _round_or_none(value, digits=4):
    return round(value, digits) if value is not None else None


# Tolerance band (as a % of entry_price) applied to every TP/SL/trailing-
# stop price comparison in classify_exit_reason -- real fills rarely land
# on the EXACT computed target (spread, slippage, the broker's own
# rounding), so a hair on the wrong side of a threshold shouldn't flip
# the classification to the wrong bucket.
_CLASSIFICATION_TOLERANCE_PCT = 0.1


def classify_exit_reason(action, entry_price, exit_price, extreme_price_since_entry, params):
    """
    Best-effort classification of WHY a position closed, applied
    retroactively against a single already-known entry/exit/extreme-
    price-since-entry rather than a full bar-by-bar replay -- mirrors
    backtest/engine.py's TP/SL/trailing-stop state machine (see
    strategy_knowledge.py's EXIT_RULES for the rationale behind each),
    but that engine tracks a position's peak price bar-by-bar as it
    happens; this reconstructs the same math after the fact from
    whatever bar history is available for the hold period.

    action: 'sell' (closing a LONG -- profit direction is UP) or 'buy'
        (closing a SHORT, forex only -- profit direction is DOWN).
    extreme_price_since_entry: the highest high reached during the hold
        for a long, or the lowest low for a short (see
        _extreme_price_since_entry) -- None if it couldn't be fetched,
        in which case trailing_stop can never be distinguished from
        stop_loss (both require knowing whether price ran further than
        the exit before coming back).

    Returns one of 'take_profit', 'stop_loss', 'trailing_stop',
    'momentum_exit' (the same four backtest/engine.py's own exit_reason
    can produce for a live position, 'end_of_data' being backtest-only),
    or None if take_profit_pct/stop_loss_pct aren't in `params`.

    THIS IS AN INFERENCE, not an authoritative report -- see
    explain_exit's docstring for why the live webhook payload can't
    just tell us which condition fired.
    """
    take_profit_pct = params.get("take_profit_pct")
    stop_loss_pct = params.get("stop_loss_pct")
    if take_profit_pct is None or stop_loss_pct is None or entry_price is None:
        return None

    closing_long = action == "sell"
    sign = 1 if closing_long else -1  # profit direction: up for a long, down for a short
    tol = abs(entry_price) * (_CLASSIFICATION_TOLERANCE_PCT / 100)

    tp_price = entry_price * (1 + sign * take_profit_pct / 100)
    sl_price = entry_price * (1 - sign * stop_loss_pct / 100)
    trail_activate_price = entry_price * (1 + sign * (take_profit_pct * 0.5) / 100)
    trail_offset = entry_price * (stop_loss_pct * 0.5) / 100

    hit_tp = (exit_price >= tp_price - tol) if closing_long else (exit_price <= tp_price + tol)
    if hit_tp:
        return "take_profit"

    trailing_was_active = False
    if extreme_price_since_entry is not None:
        trailing_was_active = (
            extreme_price_since_entry >= trail_activate_price if closing_long
            else extreme_price_since_entry <= trail_activate_price
        )

    if trailing_was_active:
        trail_stop_price = (
            extreme_price_since_entry - trail_offset if closing_long
            else extreme_price_since_entry + trail_offset
        )
        hit_trail = (exit_price <= trail_stop_price + tol) if closing_long else (exit_price >= trail_stop_price - tol)
        if hit_trail:
            return "trailing_stop"

    hit_sl = (exit_price <= sl_price + tol) if closing_long else (exit_price >= sl_price - tol)
    if hit_sl:
        return "stop_loss"

    return "momentum_exit"


def _extreme_price_since_entry(broker, symbol, entry_time_iso, timeframe, closing_long):
    """Fetches bars covering [entry_time, now] and returns the highest
    high (closing a long) or lowest low (closing a short) reached during
    the hold -- what classify_exit_reason needs to tell a trailing-stop
    exit apart from a plain stop-loss. Returns None on ANY failure
    (broker error, unparseable entry_time, no bars in range) -- callers
    must already treat that as "can't classify past take_profit/
    momentum_exit", never as a reason to fail the explanation entirely."""
    import datetime as _dt

    try:
        entry_time = _dt.datetime.fromisoformat(entry_time_iso)
        if entry_time.tzinfo is None:
            # state.trade_log's 'time' field is a naive
            # datetime.datetime.now().isoformat() -- this process (and
            # Railway's containers generally) run in UTC, so naive is
            # treated as UTC rather than left ambiguous.
            entry_time = entry_time.replace(tzinfo=_dt.timezone.utc)
        end = _dt.datetime.now(_dt.timezone.utc)
        bars = broker.get_historical_bars(symbol, timeframe=timeframe, start=entry_time, end=end)
        if not bars:
            return None
        return max(b["high"] for b in bars) if closing_long else min(b["low"] for b in bars)
    except Exception:
        return None


_EXIT_REASON_LABELS = {
    "take_profit": "take-profit target",
    "stop_loss": "stop-loss",
    "trailing_stop": "trailing stop",
    "momentum_exit": "momentum/trend-flip exit",
}


def explain_exit(action, symbol, asset_class, price, source, entry_trade=None, params=None,
                  broker=None, timeframe="1h"):
    """
    action: 'sell' (closing a long) or 'buy' (closing a short, forex only).
    source: 'webhook' | 'manual' | 'manual_close' | 'safety_stop_loss' --
        see server.py's _process_trade_signal docstring for what each
        means. manual_close and safety_stop_loss already have a
        DEFINITIVE, non-inferred reason (an operator clicked Close, or
        the safety-net threshold was breached) -- no classification
        needed or attempted for those. 'manual' (a dashboard sell button)
        is also operator-initiated, not signal-driven, so it gets the
        same treatment. Only 'webhook' -- a real TradingView-driven exit
        -- needs classify_exit_reason(), because the live webhook payload
        itself never says WHICH exit condition fired (see the module
        docstring's broader note on why this is an inference).
    entry_trade: the trade_log dict this position was opened with (found
        via the same lookup _process_trade_signal already does for pnl
        attribution), or None if no matching entry could be found (e.g.
        the position pre-dates this process's trade_log, or was opened
        by a since-restarted process before persistence caught up).
    params: the strategy params in effect for this symbol
        (db.get_symbol_strategy_assignment), needed for
        classify_exit_reason's take_profit_pct/stop_loss_pct.
    broker/timeframe: used ONLY for the 'webhook' classification path,
        to fetch the extreme price reached during the hold -- never
        called for the other sources, which don't need it.
    """
    price_str = _fmt_price(price, asset_class)

    if source == "manual_close":
        return "Exited via manual close: operator-initiated at {}.".format(price_str)
    if source == "safety_stop_loss":
        return (
            "Exited via safety-net force-close: unrealized loss breached the account-wide "
            "backstop threshold (independent of the strategy's own stop-loss), closed at {}.".format(price_str)
        )
    if source == "manual":
        return "Exited via manual trade: operator-initiated at {}.".format(price_str)
    if source == "strategy_switch":
        return (
            "Exited via strategy switch: position force-closed because the symbol's active "
            "strategy was reassigned, closed at {}.".format(price_str)
        )

    # source == 'webhook' from here -- a real TradingView-driven exit,
    # needs actual classification.
    if entry_trade is None or params is None:
        return (
            "Exited at {} -- entry details unavailable, so the exit reason (take-profit / "
            "stop-loss / trailing stop / momentum exit) couldn't be classified.".format(price_str)
        )

    try:
        entry_price = float(entry_trade["price"])
    except (KeyError, TypeError, ValueError):
        entry_price = None

    extreme_price = None
    if broker is not None and entry_price is not None:
        extreme_price = _extreme_price_since_entry(
            broker, symbol, entry_trade.get("time"), timeframe, closing_long=(action == "sell")
        )

    reason = classify_exit_reason(action, entry_price, price, extreme_price, params)

    if reason is None:
        return (
            "Exited at {} -- entry price or strategy params unavailable, so the exit reason "
            "couldn't be classified.".format(price_str)
        )

    label = _EXIT_REASON_LABELS[reason]
    pct_change = None
    if entry_price:
        sign = 1 if action == "sell" else -1
        pct_change = sign * (price - entry_price) / entry_price * 100

    pct_str = " ({}{:.2f}%)".format("+" if pct_change is not None and pct_change >= 0 else "", pct_change) \
        if pct_change is not None else ""

    if reason == "trailing_stop" and extreme_price is not None and entry_price:
        sign = 1 if action == "sell" else -1
        pct_of_target = None
        take_profit_pct = params.get("take_profit_pct")
        if take_profit_pct:
            pct_of_target = sign * (extreme_price - entry_price) / entry_price * 100 / take_profit_pct * 100
        if pct_of_target is not None:
            return (
                "Exited via {}: price reached {:.0f}% of the way to the take-profit target "
                "then pulled back, locking in {}{:.2f}% instead of giving it all back.".format(
                    label, min(pct_of_target, 999), "+" if pct_change is not None and pct_change >= 0 else "", pct_change or 0.0,
                )
            )

    return "Exited via {}: closed at {}{}.".format(label, price_str, pct_str)
