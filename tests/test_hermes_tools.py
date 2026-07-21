"""
Tests for hermes_tools.py's get_strategy_config -- specifically that it
surfaces each symbol's ACTUAL assigned strategy (including timeframe)
live from the strategies/symbol_strategy_assignments tables, not a
static hardcoded params blob. See db.py's tests (test_strategies_db.py)
for the underlying storage layer this reads from.

Also covers get_strategy_rationale -- the WHY behind the strategy's
rules (strategy_knowledge.py), distinct from get_strategy_config's
numbers.
"""

import db
import hermes_tools
import strategy_knowledge


def test_get_strategy_config_includes_symbol_strategies_with_timeframe(db_store):
    """Confirms the field is present and correct for symbols spanning
    different asset classes -- the exact scenario Hermes previously got
    wrong by inferring timeframe from signal timestamps instead of
    reading a hard fact."""
    stock_strategy = db.create_strategy(
        "Higher High Breakout - Stock", {"lookback": 7}, timeframe="30m",
    )
    forex_strategy = db.create_strategy(
        "Higher High Breakout - Forex", {"lookback": 7}, timeframe="1h",
    )
    db.assign_strategy_to_symbol("AAPL", stock_strategy["id"])
    db.assign_strategy_to_symbol("GBP_JPY", forex_strategy["id"])

    result = hermes_tools.get_strategy_config(None)

    assert result["symbol_strategies"]["AAPL"]["timeframe"] == "30m"
    assert result["symbol_strategies"]["AAPL"]["name"] == "Higher High Breakout - Stock"
    assert result["symbol_strategies"]["GBP_JPY"]["timeframe"] == "1h"
    assert result["symbol_strategies"]["GBP_JPY"]["name"] == "Higher High Breakout - Forex"


def test_get_strategy_config_no_longer_has_a_static_strategy_params_blob(db_store):
    """This key used to be a hardcoded backtest.strategy.DEFAULT_PARAMS
    constant -- guaranteed to drift the moment a symbol switched
    strategies. It's gone now; symbol_strategies (DB-backed) replaces it."""
    result = hermes_tools.get_strategy_config(None)
    assert "strategy_params" not in result
    assert "symbol_strategies" in result


def test_get_strategy_config_reflects_a_real_strategy_switch(db_store):
    """Since this reads live from the DB (not a cached/hardcoded value),
    switching a symbol's assigned strategy must be immediately visible
    on the next call -- proving it can't drift out of sync."""
    old = db.create_strategy("Higher High Breakout - Crypto", {"lookback": 7}, timeframe="30m")
    new = db.create_strategy("Higher High Breakout - Crypto Fast", {"lookback": 5}, timeframe="15m")
    db.assign_strategy_to_symbol("BTC/USD", old["id"])

    before = hermes_tools.get_strategy_config(None)
    assert before["symbol_strategies"]["BTC/USD"]["timeframe"] == "30m"

    db.assign_strategy_to_symbol("BTC/USD", new["id"])

    after = hermes_tools.get_strategy_config(None)
    assert after["symbol_strategies"]["BTC/USD"]["timeframe"] == "15m"
    assert after["symbol_strategies"]["BTC/USD"]["name"] == "Higher High Breakout - Crypto Fast"


def test_get_strategy_config_handles_a_db_error_without_raising(db_store, monkeypatch):
    """A DB hiccup here must degrade gracefully (empty symbol_strategies),
    same 'never blocks/crashes over an availability issue' rule this
    codebase applies everywhere else -- this is a read-only diagnostic
    tool, not the trade-execution path, but Hermes calling it shouldn't
    blow up a chat turn over a transient DB error either."""
    def broken_get_all_symbol_strategy_assignments():
        raise Exception("simulated DB error")

    monkeypatch.setattr(db, "get_all_symbol_strategy_assignments", broken_get_all_symbol_strategy_assignments)

    result = hermes_tools.get_strategy_config(None)
    assert result["symbol_strategies"] == {}


# --- get_strategy_rationale (the WHY, distinct from get_strategy_config) --

def test_get_strategy_rationale_matches_strategy_knowledge_describe_strategy():
    """Just a thin passthrough -- confirms it's actually wired to the
    real content, not a stub."""
    assert hermes_tools.get_strategy_rationale(None) == strategy_knowledge.describe_strategy()


def test_get_strategy_rationale_includes_the_overview_and_every_rule():
    result = hermes_tools.get_strategy_rationale(None)
    assert result["name"] == "Higher High Breakout"
    assert "overview" in result and len(result["overview"]) > 0
    for rule in ("trend_filter", "breakout", "higher_low", "rsi_filter"):
        assert rule in result["entry_rules"]
        assert "rationale" in result["entry_rules"][rule]
    for rule in ("take_profit", "stop_loss", "trailing_stop", "momentum_exit"):
        assert rule in result["exit_rules"]
        assert "rationale" in result["exit_rules"][rule]


def test_get_strategy_rationale_does_not_touch_the_db(db_store, monkeypatch):
    """Static content (same for every symbol/strategy version) -- unlike
    get_strategy_config, this must not need a DB round-trip at all."""
    def fail_if_called(*a, **k):
        raise AssertionError("get_strategy_rationale should never touch the DB")

    monkeypatch.setattr(db, "get_all_symbol_strategy_assignments", fail_if_called)
    hermes_tools.get_strategy_rationale(None)  # must not raise
