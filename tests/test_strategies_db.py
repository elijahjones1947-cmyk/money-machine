"""
Tests for db.py's strategy definition + per-symbol assignment storage
(Phase 0 of the strategy-overhaul work): immutable versioned strategy
rows, and the symbol -> active-strategy mapping used by explanation
generation (Phase 1/3) and /webhook's strategy_id validation (Phase 4).
"""

import db


def test_create_strategy_starts_at_version_1(db_store):
    row = db.create_strategy("Higher High Breakout - Stock", {"lookback": 7})
    assert row["name"] == "Higher High Breakout - Stock"
    assert row["version"] == 1


def test_create_strategy_increments_version_for_same_name(db_store):
    db.create_strategy("HHB - Stock", {"lookback": 7})
    second = db.create_strategy("HHB - Stock", {"lookback": 10})
    assert second["version"] == 2


def test_create_strategy_does_not_mutate_earlier_versions(db_store):
    """The whole point of the versioning model: creating v2 must leave
    v1's row (and whatever trade.strategy_id points at it) completely
    untouched."""
    first = db.create_strategy("HHB - Stock", {"lookback": 7})
    db.create_strategy("HHB - Stock", {"lookback": 10})

    v1 = db.get_strategy(first["id"])
    assert v1["params"]["lookback"] == 7
    assert v1["version"] == 1


def test_create_strategy_versions_are_independent_per_name(db_store):
    stock = db.create_strategy("HHB - Stock", {"lookback": 7})
    crypto = db.create_strategy("HHB - Crypto", {"lookback": 7})
    assert stock["version"] == 1
    assert crypto["version"] == 1


def test_get_strategy_round_trips_params(db_store):
    created = db.create_strategy("HHB - Forex", {"lookback": 7, "take_profit_pct": 0.2, "use_rsi_filter": True})
    fetched = db.get_strategy(created["id"])
    assert fetched["params"] == {"lookback": 7, "take_profit_pct": 0.2, "use_rsi_filter": True}


def test_get_strategy_returns_none_for_unknown_id(db_store):
    assert db.get_strategy(99999) is None


def test_list_strategies_newest_first(db_store):
    first = db.create_strategy("A", {})
    second = db.create_strategy("B", {})
    ids = [r["id"] for r in db.list_strategies()]
    assert ids == [second["id"], first["id"]]


def test_get_latest_strategy_version_returns_highest_version(db_store):
    db.create_strategy("HHB - Stock", {"lookback": 7})
    v2 = db.create_strategy("HHB - Stock", {"lookback": 10})
    latest = db.get_latest_strategy_version("HHB - Stock")
    assert latest["id"] == v2["id"]
    assert latest["version"] == 2


def test_assign_strategy_to_symbol_and_read_it_back(db_store):
    strategy = db.create_strategy("HHB - Stock", {"lookback": 7})
    db.assign_strategy_to_symbol("AAPL", strategy["id"])

    assignment = db.get_symbol_strategy_assignment("AAPL")
    assert assignment["id"] == strategy["id"]
    assert assignment["params"] == {"lookback": 7}


def test_get_symbol_strategy_assignment_returns_none_when_unassigned(db_store):
    assert db.get_symbol_strategy_assignment("AAPL") is None


def test_assign_strategy_to_symbol_overwrites_previous_assignment(db_store):
    """Re-assigning a symbol (the mechanism Phase 4's strategy-switch
    uses) must replace, not add to, its assignment -- a symbol has
    exactly one active strategy at a time."""
    old = db.create_strategy("HHB - Stock v1", {"lookback": 7})
    new = db.create_strategy("HHB - Stock v2", {"lookback": 10})

    db.assign_strategy_to_symbol("AAPL", old["id"])
    db.assign_strategy_to_symbol("AAPL", new["id"])

    assignment = db.get_symbol_strategy_assignment("AAPL")
    assert assignment["id"] == new["id"]


def test_get_all_symbol_strategy_assignments(db_store):
    stock_strategy = db.create_strategy("HHB - Stock", {"lookback": 7})
    crypto_strategy = db.create_strategy("HHB - Crypto", {"lookback": 7})
    db.assign_strategy_to_symbol("AAPL", stock_strategy["id"])
    db.assign_strategy_to_symbol("BTC/USD", crypto_strategy["id"])

    all_assignments = {a["symbol"]: a["id"] for a in db.get_all_symbol_strategy_assignments()}
    assert all_assignments == {"AAPL": stock_strategy["id"], "BTC/USD": crypto_strategy["id"]}


def test_save_trade_persists_explanation_and_strategy_id(db_store):
    strategy = db.create_strategy("HHB - Stock", {"lookback": 7})
    db.save_trade(
        "buy", "AAPL", "stock", 10, 200.0, pnl=None, regime="trending",
        source="webhook", explanation="Entered long: broke the 7-bar high.",
        strategy_id=strategy["id"],
    )
    rows = db.get_recent_trades(limit=1)
    assert rows[0]["explanation"] == "Entered long: broke the 7-bar high."
    assert rows[0]["strategy_id"] == strategy["id"]


def test_save_trade_explanation_and_strategy_id_are_optional(db_store):
    """Every existing call site (backfilled trades, tests) must keep
    working without passing these -- no regression on the pre-Phase-0
    save_trade signature."""
    db.save_trade("buy", "AAPL", "stock", 10, 200.0)
    rows = db.get_recent_trades(limit=1)
    assert rows[0]["explanation"] is None
    assert rows[0]["strategy_id"] is None


# --- seed_default_strategies() (server.py) --------------------------------
# db_store's strategies list is empty at the start of every test (reset_state
# clears it), same situation as recover_pending_webhook_signals() in the
# durable-queue tests -- call the bootstrap function directly to test it,
# same pattern used there.

def test_seed_default_strategies_creates_one_per_asset_class(db_store):
    import server

    server.seed_default_strategies()

    names = {s["name"] for s in db.list_strategies()}
    assert names == {
        "Higher High Breakout - Stock",
        "Higher High Breakout - Forex",
        "Higher High Breakout - Crypto",
    }


def test_seed_default_strategies_assigns_every_watched_symbol(db_store):
    import server
    import state

    server.seed_default_strategies()

    for asset_class, symbols in state.watched_symbols.items():
        strategy = db.get_latest_strategy_version(
            "Higher High Breakout - {}".format(asset_class.capitalize())
        )
        for symbol in symbols:
            assignment = db.get_symbol_strategy_assignment(symbol)
            assert assignment is not None, "{} was not assigned a strategy".format(symbol)
            assert assignment["id"] == strategy["id"]


def test_seed_default_strategies_params_match_observed_live_values(db_store):
    """Pins the exact params this bootstrap uses to what's actually been
    observed live on TradingView's alert log -- a silent drift here would
    make every generated explanation subtly wrong."""
    import server

    server.seed_default_strategies()

    stock = db.get_latest_strategy_version("Higher High Breakout - Stock")
    assert stock["params"]["lookback"] == 7
    assert stock["params"]["breakout_buffer_pct"] == 0.05
    assert stock["params"]["take_profit_pct"] == 0.6
    assert stock["params"]["stop_loss_pct"] == 0.35

    crypto = db.get_latest_strategy_version("Higher High Breakout - Crypto")
    assert crypto["params"]["breakout_buffer_pct"] == 0.15
    assert crypto["params"]["take_profit_pct"] == 2

    forex = db.get_latest_strategy_version("Higher High Breakout - Forex")
    assert forex["params"]["breakout_buffer_pct"] == 0.02
    assert forex["params"]["take_profit_pct"] == 0.2


def test_seed_default_strategies_is_a_noop_if_any_strategy_exists(db_store):
    """Must never overwrite real strategy history or an operator's own
    edits -- checked by seeding once, manually altering the assignment,
    then confirming a second call changes nothing."""
    import server

    server.seed_default_strategies()
    stock_strategy = db.get_latest_strategy_version("Higher High Breakout - Stock")

    # Simulate an operator having since created their own strategy and
    # reassigned AAPL to it.
    custom = db.create_strategy("My Custom Tune", {"lookback": 5})
    db.assign_strategy_to_symbol("AAPL", custom["id"])

    server.seed_default_strategies()  # must be a no-op

    assert db.get_symbol_strategy_assignment("AAPL")["id"] == custom["id"]
    assert len(db.list_strategies()) == 4  # 3 seeded + the 1 custom one, no duplicates
    assert db.get_latest_strategy_version("Higher High Breakout - Stock")["id"] == stock_strategy["id"]
