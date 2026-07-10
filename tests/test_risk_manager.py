"""
Tests for risk/risk_manager.py -- the single most important piece of
code in this codebase to have covered, since it's already caused two
real production incidents this build: the sell-side caps being applied
to closing trades (fixed by the reduces_position bypass), and the
risk_percent/cap drift that silently blocked every forex trade for two
days (fixed by the sizing clamp in server.py, but the underlying
check_trade behavior needs to stay correct for that fix to matter).

No Flask/DB/broker network calls needed -- RiskManager only needs a
broker object with get_account_info()/get_positions()/get_price(), so
a small fake stands in for the real Alpaca/OANDA brokers.
"""

import pytest

from risk.risk_manager import RiskManager


RISK_CONFIG = {
    "stock": {"max_position_size_pct": 0.10, "max_daily_loss_pct": 0.05, "max_open_positions": 5},
    "forex": {"max_position_size_pct": 0.05, "max_daily_loss_pct": 0.03, "max_open_positions": 3, "max_leverage": 20},
    "crypto": {"max_position_size_pct": 0.03, "max_daily_loss_pct": 0.02, "max_open_positions": 3},
    "account_wide": {"max_daily_loss_pct": 0.08},
}


class FakeBroker:
    def __init__(self, equity=10000.0, positions=None, price=100.0):
        self.equity = equity
        self.positions = positions or []
        self.price = price

    def get_account_info(self):
        return {"equity": self.equity, "buying_power": self.equity, "last_equity": self.equity}

    def get_positions(self):
        return self.positions

    def get_price(self, symbol):
        return self.price


@pytest.fixture
def risk_manager():
    rm = RiskManager({k: dict(v) for k, v in RISK_CONFIG.items()})
    rm.set_starting_equity(10000.0)
    return rm


def test_normal_trade_within_caps_is_approved(risk_manager):
    approved, reason = risk_manager.check_trade(FakeBroker(), "AAPL", "buy", 5, "stock", price=100.0)
    assert approved, reason


def test_oversized_position_is_rejected(risk_manager):
    # $200 * 100 = $20,000 position on $10,000 equity, 10% cap = $1,000 max
    approved, reason = risk_manager.check_trade(FakeBroker(), "AAPL", "buy", 200, "stock", price=100.0)
    assert not approved
    assert "too big" in reason.lower()


def test_max_open_positions_rejects_new_buys(risk_manager):
    five_positions = [object()] * 5  # count is all that matters, len() only
    broker = FakeBroker(positions=five_positions)
    approved, reason = risk_manager.check_trade(broker, "AAPL", "buy", 1, "stock", price=100.0)
    assert not approved
    assert "max open positions" in reason.lower()


def test_leverage_cap_rejects_when_it_is_the_binding_constraint():
    """Uses a config where max_leverage is deliberately the tighter
    constraint (2x) relative to max_position_size_pct (300%), so
    leverage is what actually binds -- proves the leverage-check CODE
    is correct in isolation.

    NOTE, a real finding from writing this test: with the ACTUAL
    production config (forex: 5% position-size cap, 20x leverage cap),
    max_leverage can never be the binding constraint. leverage is
    computed as position_value / equity -- the exact same ratio
    max_position_size_pct already caps -- so whenever
    max_position_size_pct (as a fraction, 0.05) is less than
    max_leverage (as a multiple, 20), the size cap always fires first.
    The leverage check isn't wrong, it's just unreachable given today's
    real numbers. Not "fixing" this here since it's a live risk
    parameter, not a test -- flagging it as a genuine discovered issue
    rather than silently coding around it.
    """
    rm = RiskManager({
        "forex": {"max_position_size_pct": 3.0, "max_daily_loss_pct": 0.03, "max_open_positions": 3, "max_leverage": 2},
        "account_wide": {"max_daily_loss_pct": 0.08},
    })
    rm.set_starting_equity(1000.0)
    # $2,500 position on $1,000 equity: 2.5x leverage (over the 2x cap),
    # 250% of equity (under the 300% size cap) -- leverage is the only
    # thing that can reject this.
    approved, reason = rm.check_trade(FakeBroker(equity=1000.0), "EUR_USD", "buy", 2500, "forex", price=1.0)
    assert not approved
    assert "leverage" in reason.lower()


def test_daily_loss_halts_only_that_asset_class(risk_manager):
    # Push stock's daily P&L past its 5% cap on $10,000 equity (-$500)
    risk_manager.record_trade_result("stock", -600.0, 9400.0)
    approved, reason = risk_manager.check_trade(FakeBroker(equity=9400.0), "AAPL", "buy", 1, "stock", price=100.0)
    assert not approved
    assert risk_manager.trading_halted["stock"] is True
    # Forex should be untouched -- a bad day in one asset class shouldn't
    # shut down the others.
    assert risk_manager.trading_halted["forex"] is False
    approved_forex, _ = risk_manager.check_trade(FakeBroker(equity=9400.0), "EUR_USD", "buy", 10, "forex", price=1.0)
    assert approved_forex


def test_account_wide_halt_blocks_every_asset_class(risk_manager):
    # -9% swing against an 8% account-wide cap
    risk_manager.record_trade_result("stock", -900.0, 9100.0)
    assert risk_manager.account_halted is True
    for asset_class in ("stock", "forex", "crypto"):
        approved, reason = risk_manager.check_trade(FakeBroker(equity=9100.0), "X", "buy", 1, asset_class, price=10.0)
        assert not approved
        assert "account-wide" in reason.lower()


def test_reduces_position_bypasses_every_check_including_halts(risk_manager):
    """This is the fix for a real bug: closing/reducing a position must
    never be blocked by risk management, even during a halt -- risk
    checks exist to prevent taking on MORE risk, and blocking an exit
    during a loss spiral is exactly backwards. This is also what makes
    the position safety-net monitor (server.py) reliable: it needs to
    be able to force-close a position even when trading is halted."""
    risk_manager.record_trade_result("stock", -900.0, 9100.0)
    assert risk_manager.account_halted is True

    # A close far bigger than the position-size cap, during an active halt
    approved, reason = risk_manager.check_trade(
        FakeBroker(equity=9100.0), "AAPL", "sell", 500, "stock", price=100.0, reduces_position=True,
    )
    assert approved, reason


def test_price_drift_uses_callers_snapshot_not_a_fresh_fetch(risk_manager):
    # Broker's own get_price would return 200 (way over cap), but the
    # caller's snapshot price of 10 should be what's actually used --
    # avoids a correctly-sized trade getting rejected because price
    # ticked between sizing and validation.
    broker = FakeBroker(price=200.0)
    approved, reason = risk_manager.check_trade(broker, "BTC/USD", "buy", 5, "stock", price=10.0)
    assert approved, reason
