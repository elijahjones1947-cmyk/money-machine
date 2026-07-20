"""
Encodes WHY the Higher High Breakout strategy's rules exist, as
structured data trade_explanations.py (and anything else — Hermes,
the dashboard) can reference to generate rationale text, rather than
re-deriving "why does this rule matter" ad hoc every time explanation
wording needs to change.

This module is pure documentation/metadata — it has no logic of its
own and never computes a trading decision. The actual signal math lives
in backtest/strategy.py (compute_signals, the live Python port of the
Pine entry/exit conditions) and backtest/engine.py (the TP/SL/trailing-
stop state machine, mirrored from the live script's EXITS section, see
commit b09bc16). This module only explains what those already mean.

Each entry RULE below corresponds 1:1 to a boolean computed by
backtest.strategy.compute_signals() for a given bar:
    trend_bullish        -> ENTRY_RULES["trend_filter"]
    higher_high_breakout -> ENTRY_RULES["breakout"]
    higher_low           -> ENTRY_RULES["higher_low"]
    rsi_ok                -> ENTRY_RULES["rsi_filter"]
(buy_condition is the AND of all four.)

Each EXIT_RULE corresponds to one of backtest.engine.py's exit_reason
values ("take_profit", "stop_loss", "trailing_stop", "momentum_exit") —
see trade_explanations.py for how a live exit gets classified into one
of these after the fact (the live webhook payload doesn't self-report
which one fired; see that module's docstring for why).
"""

STRATEGY_NAME = "Higher High Breakout"

STRATEGY_OVERVIEW = (
    "Trend-following breakout strategy: waits for price to already be in a "
    "confirmed uptrend (fast EMA above slow EMA), THEN requires it to break "
    "out above its own recent trading range with a small buffer (filtering "
    "noise-level breaks) and hold a rising low (avoiding a breakout that's "
    "already failing). An optional RSI floor screens out weak-momentum "
    "breakouts. The idea is to enter continuation moves that are already "
    "underway and already confirmed, not to predict a reversal or catch a "
    "bottom -- it will typically be late to any given move and is fine with "
    "that trade-off in exchange for fewer false starts."
)

# --- Entry rules -----------------------------------------------------------
# Keyed to match backtest.strategy.compute_signals()'s per-bar signal dict
# and DEFAULT_PARAMS -- see this module's docstring above.

ENTRY_RULES = {
    "trend_filter": {
        "rule": "ema_fast > ema_slow",
        "params_used": ["ema_fast_length", "ema_slow_length"],
        "rationale": (
            "The fast EMA sitting above the slow EMA means recent price action "
            "is outpacing the longer-term average -- a live uptrend, not just a "
            "single green candle. Trading breakouts only WITH this filter avoids "
            "buying breakouts that occur against the prevailing trend, which "
            "historically fail more often than they follow through."
        ),
    },
    "breakout": {
        "rule": "close > recent_high * (1 + breakout_buffer_pct / 100)",
        "params_used": ["lookback", "breakout_buffer_pct"],
        "rationale": (
            "A close above the recent N-bar high is the actual 'higher high' "
            "the strategy is named for -- proof buyers pushed price past a "
            "level that had capped it for the lookback window. The buffer "
            "(rather than a bare break of the exact high) exists specifically "
            "to filter out breaks that are within normal noise/spread and "
            "reverse immediately."
        ),
    },
    "higher_low": {
        "rule": "low > recent_low",
        "params_used": ["lookback"],
        "rationale": (
            "Confirms the breakout isn't happening on top of a low that's "
            "ALSO breaking down -- a rising low alongside a rising high means "
            "the whole recent range is shifting up, not just spiking on one "
            "bar. Without this, a breakout bar with a lower low would be a "
            "wide-range reversal candle, not a clean continuation."
        ),
    },
    "rsi_filter": {
        "rule": "rsi >= rsi_min (only checked if use_rsi_filter is True)",
        "params_used": ["use_rsi_filter", "rsi_length", "rsi_min"],
        "rationale": (
            "A minimum RSI floor screens out breakouts happening on fading "
            "momentum (price technically broke the level, but the move up "
            "has already lost steam by every-bar-momentum measures) -- these "
            "tend to be exactly the breakouts that stall and reverse."
        ),
    },
}

# --- Exit rules --------------------------------------------------------
# Every exit here is evaluated against the SAME entry_price + strategy
# params; see trade_explanations.py's classify_exit_reason() for how a
# live exit's actual price action gets matched to one of these after
# the fact.

EXIT_RULES = {
    "take_profit": {
        "rule": "price reaches entry_price * (1 + take_profit_pct / 100)",
        "params_used": ["take_profit_pct"],
        "rationale": (
            "A fixed profit target locks in gains at a predetermined level "
            "rather than hoping a winning trade keeps running indefinitely -- "
            "the strategy's stated edge is catching confirmed continuation, "
            "not perfectly timing a top."
        ),
    },
    "stop_loss": {
        "rule": "price reaches entry_price * (1 - stop_loss_pct / 100)",
        "params_used": ["stop_loss_pct"],
        "rationale": (
            "A fixed loss limit caps how wrong a single trade is allowed to "
            "be -- if the breakout fails and price falls back through the "
            "level it broke, this exits before the loss compounds. This is "
            "the STRATEGY's own intended stop, distinct from the account-wide "
            "safety-net backstop (config.py's safety_stop_loss_pct, see "
            "server.py's run_position_safety_checks) which is deliberately "
            "looser and only exists to catch a missed/failed exit signal."
        ),
    },
    "trailing_stop": {
        "rule": (
            "once price reaches entry_price * (1 + take_profit_pct * 0.5 / 100), "
            "a trailing stop activates at peak_price_since_entry - "
            "(entry_price * stop_loss_pct * 0.5 / 100), and re-tightens as the "
            "peak rises"
        ),
        "params_used": ["take_profit_pct", "stop_loss_pct"],
        "rationale": (
            "Once a trade is already halfway to its profit target, giving up "
            "on the FULL target in exchange for locking in a meaningfully "
            "smaller guaranteed give-back (half the stop-loss distance) is a "
            "better trade-off than risking the fixed stop-loss round-tripping "
            "an already-large unrealized gain back to a loss. This is what "
            "lets the strategy occasionally run further than take_profit_pct "
            "on a strong move while still protecting most of the gain if it "
            "reverses instead."
        ),
    },
    "momentum_exit": {
        "rule": "trend_bearish (ema_fast < ema_slow) OR close < ema_fast",
        "params_used": ["ema_fast_length", "ema_slow_length"],
        "rationale": (
            "The same trend filter that gates entries also gates exits: once "
            "price falls back below the fast EMA (or the EMAs themselves "
            "cross bearish), the trend-continuation premise the trade was "
            "entered on no longer holds, independent of whether TP or SL has "
            "been hit yet -- there's no reason to keep holding a continuation "
            "trade once the continuation itself has stalled."
        ),
    },
}


def describe_strategy():
    """Full structured description -- overview + every entry/exit rule with
    its rationale. Suitable for a dashboard 'how this strategy works' panel
    or a Hermes tool response (see hermes_tools.py's get_strategy_config,
    which currently only returns raw params -- this is the narrative
    counterpart)."""
    return {
        "name": STRATEGY_NAME,
        "overview": STRATEGY_OVERVIEW,
        "entry_rules": ENTRY_RULES,
        "exit_rules": EXIT_RULES,
    }
