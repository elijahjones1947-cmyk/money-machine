"""
Tests for hermes_tools.py's get_strategy_config -- specifically that it
surfaces each symbol's ACTUAL assigned strategy (including timeframe)
live from the strategies/symbol_strategy_assignments tables, not a
static hardcoded params blob. See db.py's tests (test_strategies_db.py)
for the underlying storage layer this reads from.
"""

import db
import hermes_tools


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
