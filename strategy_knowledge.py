"""
Encodes WHY each strategy family's rules exist, as structured data
trade_explanations.py (and anything else — Hermes, the dashboard) can
reference to generate rationale text, rather than re-deriving "why
does this rule matter" ad hoc every time explanation wording needs to
change.

This module is pure documentation/metadata — it has no logic of its
own and never computes a trading decision. The actual signal math for
Higher High Breakout lives in backtest/strategy.py (compute_signals,
the live Python port of the Pine entry/exit conditions) and
backtest/engine.py (the TP/SL/trailing-stop state machine, mirrored
from the live script's EXITS section, see commit b09bc16). This module
only explains what those already mean. Kev's ICC (added when it was
built as a second, unassigned strategy family — see
pinescript/kevs_icc_strategy.pine) has no Python port at all yet: it
only exists here as a description for Hermes to draw on, since nothing
downstream (compute_signals, the intrabar exit poller) understands its
structure-based signals yet — that's expected to stay true until Eli
actually assigns a symbol to it and that gap gets closed as its own
piece of work.

Each Higher High Breakout entry RULE below corresponds 1:1 to a
boolean computed by backtest.strategy.compute_signals() for a given
bar:
    trend_bullish        -> ENTRY_RULES["trend_filter"]
    higher_high_breakout -> ENTRY_RULES["breakout"]
    higher_low           -> ENTRY_RULES["higher_low"]
    rsi_ok                -> ENTRY_RULES["rsi_filter"]
(buy_condition is the AND of all four.)

Each Higher High Breakout EXIT_RULE corresponds to one of
backtest.engine.py's exit_reason values ("take_profit", "stop_loss",
"trailing_stop", "momentum_exit") — see trade_explanations.py for how a
live exit gets classified into one of these after the fact (the live
webhook payload doesn't self-report which one fired; see that module's
docstring for why).

describe_strategy(name) looks up any strategy family registered in
_STRATEGIES below by its exact strategies.name (the same name a
strategies-table row / symbol_strategy_assignments join would report) —
defaulting to Higher High Breakout when called with no argument, for
every caller that predates multi-strategy support.
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


# --- Kev's ICC (Indication / Correction / Continuation) --------------------
# See pinescript/kevs_icc_strategy.pine for the actual Pine implementation
# this describes. Mapped onto the same entry_rules/exit_rules shape as
# Higher High Breakout above (indication + correction + continuation +
# the optional daily filter as "entry" rules; stop-loss + take-profit as
# "exit" rules) purely so describe_strategy()'s callers never have to
# branch on which strategy they asked about -- the shape is always the
# same regardless of family.

ICC_STRATEGY_NAME = "Kev's ICC"

ICC_STRATEGY_OVERVIEW = (
    "Structure-based sequence strategy (Indication / Correction / "
    "Continuation): waits for a confirmed swing high or low to break the "
    "prior swing (Indication), then requires a full candle CLOSE back "
    "beyond that level in the opposite direction (Correction -- often a "
    "liquidity grab against early breakout traders), and only enters once "
    "price closes back through the same level a THIRD time in the "
    "original direction (Continuation). Pure price-structure and close-"
    "confirmation based -- deliberately no Fibonacci zones or ADX/momentum "
    "filters (an earlier generic ICT-style version used those and didn't "
    "match Kev's actual rules). The framework's known deviation risk isn't "
    "the rules themselves -- it's entering early on a wick before "
    "Correction fully confirms, or negotiating exceptions mid-trade -- "
    "which is exactly why every stage requires a full-candle close, not a "
    "touch, to advance."
)

ICC_ENTRY_RULES = {
    "indication": {
        "rule": "a new confirmed swing high/low breaks the prior swing high/low on the entry timeframe (4H)",
        "params_used": ["pivotLenL", "pivotLenR"],
        "rationale": (
            "A break of the prior structure point is the first sign the prevailing "
            "range is shifting -- but on its own it's just a marker, not a trade: "
            "the framework deliberately does NOT act on this alone, since a fresh "
            "break can still just be a stop-hunt that immediately reverses."
        ),
    },
    "correction": {
        "rule": "a FULL candle CLOSE back beyond the Indication level (a wick does NOT count)",
        "params_used": ["pivotLenL", "pivotLenR"],
        "rationale": (
            "Requiring a full close, not just a touch, is what stops the framework "
            "from reacting to a liquidity grab against early breakout traders as if "
            "it were a real reversal. No confirmed close beyond the level means no "
            "valid Correction, which means no possible Continuation either -- this "
            "is the single gate that exists specifically to prevent entering before "
            "the pullback has actually confirmed."
        ),
    },
    "continuation": {
        "rule": "price closes back through the Indication level a second time, in the ORIGINAL direction -- the only entry trigger",
        "params_used": ["pivotLenL", "pivotLenR"],
        "rationale": (
            "Only once the pullback (Correction) has itself been confirmed does a "
            "close back through the level in the original direction mean the "
            "original move is actually resuming, rather than the breakout simply "
            "failing. This is the sole entry trigger in the whole framework -- "
            "there is no earlier or alternate entry point."
        ),
    },
    "daily_filter": {
        "rule": "optional: only take a 4H Continuation aligned with the daily trend (higher-highs/higher-lows for longs, lower-highs/lower-lows for shorts across the last several confirmed daily swings); skipped entirely if daily is ranging with no clear bias",
        "params_used": ["dailyPivotLenL", "dailyPivotLenR", "dailySwingsForBias"],
        "rationale": (
            "Trend persistence rule: structure doesn't flip on a single rally or "
            "dip -- the daily trend is read from CONFIRMED swing points, which only "
            "move when price actually closes beyond the most recent opposing "
            "structure point, so a large-looking pullback that hasn't done that yet "
            "is still just a Correction, not a trend change. This filter is "
            "optional specifically because skipping it trades every valid 4H signal "
            "on its own merits, at the cost of alignment with the bigger picture."
        ),
    },
}

ICC_EXIT_RULES = {
    "stop_loss": {
        "rule": "beyond the Correction's own high/low (the 4H structure that would invalidate the setup if broken further)",
        "params_used": ["slBufferPct"],
        "rationale": (
            "If price re-breaks past the Correction's own extreme, the premise "
            "the Continuation was entered on (that the Correction was just a "
            "pullback, not the start of a genuine reversal) is wrong -- the stop "
            "sits exactly at the structural point that proves that."
        ),
    },
    "take_profit": {
        "rule": "the next daily reaction zone (prior daily swing high/low) if the daily filter is on; otherwise the next opposing 4H structure level in the trade's direction",
        "params_used": [],
        "rationale": (
            "Targets are read off real prior structure (a level price has already "
            "reacted to before) rather than a fixed percentage, on the premise that "
            "genuine supply/demand zones are more predictive of where a move stalls "
            "than an arbitrary distance would be."
        ),
    },
}

_STRATEGIES = {
    STRATEGY_NAME: {
        "overview": STRATEGY_OVERVIEW,
        "entry_rules": ENTRY_RULES,
        "exit_rules": EXIT_RULES,
    },
    ICC_STRATEGY_NAME: {
        "overview": ICC_STRATEGY_OVERVIEW,
        "entry_rules": ICC_ENTRY_RULES,
        "exit_rules": ICC_EXIT_RULES,
    },
}


def describe_strategy(name=None):
    """Full structured description -- overview + every entry/exit rule with
    its rationale -- for the strategy family matching `name` (an exact
    strategies.name, e.g. "Higher High Breakout" or "Kev's ICC"). Suitable
    for a dashboard 'how this strategy works' panel or a Hermes tool
    response (see hermes_tools.py's get_strategy_config, which only
    returns raw params -- this is the narrative counterpart).

    Defaults to Higher High Breakout (this module's original strategy)
    when `name` is omitted, so every caller written before a second
    strategy family existed keeps working unchanged. An unrecognized name
    returns an {"error": ...} dict rather than raising -- asking about a
    strategy this module doesn't have rationale for yet is a normal,
    expected outcome (e.g. a brand-new custom variant Eli just created
    through the dashboard), not a bug to crash on."""
    resolved_name = name or STRATEGY_NAME
    entry = _STRATEGIES.get(resolved_name)
    if entry is None:
        return {
            "error": "no rationale on file for {!r}".format(resolved_name),
            "known_strategies": sorted(_STRATEGIES.keys()),
        }
    return {
        "name": resolved_name,
        "overview": entry["overview"],
        "entry_rules": entry["entry_rules"],
        "exit_rules": entry["exit_rules"],
    }
