"""
Tests for strategy_knowledge.py -- pure metadata, no logic, so these are
mostly structural sanity checks: every rule strategy.py's compute_signals
actually evaluates has a corresponding entry here, and describe_strategy()
returns something a caller (Hermes, trade_explanations.py) can rely on
without extra None-checking.
"""

import strategy_knowledge as sk
from backtest.strategy import DEFAULT_PARAMS


def test_describe_strategy_has_the_expected_shape():
    d = sk.describe_strategy()
    assert d["name"] == sk.STRATEGY_NAME
    assert isinstance(d["overview"], str) and len(d["overview"]) > 0
    assert d["entry_rules"] == sk.ENTRY_RULES
    assert d["exit_rules"] == sk.EXIT_RULES


def test_every_entry_rule_has_rule_text_and_rationale():
    for key, rule in sk.ENTRY_RULES.items():
        assert rule["rule"], "{} has no rule text".format(key)
        assert rule["rationale"], "{} has no rationale".format(key)
        assert rule["params_used"], "{} lists no params_used".format(key)


def test_every_exit_rule_has_rule_text_and_rationale():
    for key, rule in sk.EXIT_RULES.items():
        assert rule["rule"], "{} has no rule text".format(key)
        assert rule["rationale"], "{} has no rationale".format(key)
        assert rule["params_used"], "{} lists no params_used".format(key)


def test_entry_rules_cover_every_boolean_compute_signals_evaluates():
    """compute_signals()'s buy_condition is the AND of trend_bullish,
    higher_high_breakout, higher_low, and rsi_ok -- each needs a
    corresponding ENTRY_RULES entry or an explanation generator would
    have nothing to say about it."""
    assert set(sk.ENTRY_RULES.keys()) == {"trend_filter", "breakout", "higher_low", "rsi_filter"}


def test_exit_rules_cover_every_backtest_engine_exit_reason():
    """Must match backtest/engine.py's exit_reason values exactly
    (minus 'end_of_data', which is backtest-only and can't occur live)."""
    assert set(sk.EXIT_RULES.keys()) == {"take_profit", "stop_loss", "trailing_stop", "momentum_exit"}


def test_all_params_used_are_real_default_params_keys():
    """Every params_used entry must be a real key in
    backtest.strategy.DEFAULT_PARAMS -- a typo here would silently
    reference a parameter that doesn't exist."""
    all_rules = list(sk.ENTRY_RULES.values()) + list(sk.EXIT_RULES.values())
    for rule in all_rules:
        for param_name in rule["params_used"]:
            assert param_name in DEFAULT_PARAMS, "{!r} is not a real strategy param".format(param_name)
