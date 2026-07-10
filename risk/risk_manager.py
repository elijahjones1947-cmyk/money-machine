class RiskManager:
    """
    Sits between a strategy's decision and the broker's execution.
    Tracks daily P&L and halt status SEPARATELY per asset class
    (stock / forex / crypto / ...) so a bad day in one doesn't shut
    down the others, plus one account-wide breaker that overrides
    everything if your TOTAL combined equity (across all distinct
    brokers) drops too far in a day.

    Asset classes are derived from whatever keys config has (besides
    "account_wide"), so adding a new asset class is just a matter of
    adding a new bucket to RISK_CONFIG — nothing here needs to change.
    """

    def __init__(self, config):
        # config = {
        #   "stock": {"max_position_size_pct": .., "max_daily_loss_pct": .., "max_open_positions": ..},
        #   "forex": {"max_position_size_pct": .., "max_daily_loss_pct": .., "max_open_positions": .., "max_leverage": ..},
        #   "crypto": {"max_position_size_pct": .., "max_daily_loss_pct": .., "max_open_positions": ..},
        #   "account_wide": {"max_daily_loss_pct": ..}
        # }
        self.config = config
        self.asset_classes = [k for k in config.keys() if k != "account_wide"]
        self.daily_pnl = {k: 0.0 for k in self.asset_classes}
        self.trading_halted = {k: False for k in self.asset_classes}
        self.account_halted = False
        self.starting_equity_today = None

    def set_starting_equity(self, total_equity):
        """Call once per day (e.g. on first trade or a daily reset job)."""
        self.starting_equity_today = total_equity

    def check_trade(self, broker, symbol, side, size, asset_class, price=None):
        """Returns (approved: bool, reason: str).

        `price` should be the SAME price snapshot the caller used to size
        the trade. If omitted, we fetch our own — but for fast-moving
        assets (crypto especially) that can cause a price-drift mismatch
        where a correctly-sized trade gets rejected because the price
        ticked between sizing and validation. Always pass price when you
        have it.
        """
        if self.account_halted:
            return False, "ACCOUNT-WIDE halt active - all trading stopped"

        if self.trading_halted[asset_class]:
            return False, "{} trading halted - daily loss limit hit".format(asset_class)

        rules = self.config[asset_class]
        account = broker.get_account_info()

        # per-asset-class daily loss check
        if self.daily_pnl[asset_class] <= -abs(account["equity"] * rules["max_daily_loss_pct"]):
            self.trading_halted[asset_class] = True
            return False, "{} daily loss limit exceeded, halting {} only".format(asset_class, asset_class)

        # position size cap — use the caller's price snapshot if given,
        # so sizing and validation agree even on fast-moving assets.
        # Skipped for sells: this cap exists to stop OPENING an
        # oversized position, not to block CLOSING one you already
        # hold. A sell sized to your actual held quantity (see
        # server.py's _get_held_qty) can legitimately be worth more
        # than max_position_size_pct if equity has since dropped or the
        # position was opened before risk settings changed -- refusing
        # to let you close it in that case is backwards from a risk
        # management standpoint.
        if price is None:
            price = broker.get_price(symbol)
        position_value = float(size) * price
        max_allowed = account["equity"] * rules["max_position_size_pct"]
        if side != "sell" and position_value > max_allowed:
            return False, "Position too big: ${:.2f} > max ${:.2f}".format(position_value, max_allowed)

        # max open positions — same reasoning: closing a position never
        # increases your open-position count, so a sell shouldn't be
        # blocked by a cap meant to stop opening MORE positions.
        if side != "sell":
            current_positions = broker.get_positions()
            if len(current_positions) >= rules["max_open_positions"]:
                return False, "Max open positions reached for {}".format(asset_class)

        # leverage check (mainly relevant for forex; crypto/stock configs
        # simply omit max_leverage, so this is skipped for them)
        leverage_cap = rules.get("max_leverage")
        if leverage_cap:
            leverage = position_value / account["equity"]
            if leverage > leverage_cap:
                return False, "Leverage would exceed {}x cap".format(leverage_cap)

        return True, "OK"

    def record_trade_result(self, asset_class, pnl, total_account_equity):
        """
        Call after every trade closes (or on a periodic equity check).
        total_account_equity = COMBINED equity across all DISTINCT brokers
        (stock + crypto share one Alpaca balance, so don't double-count).
        """
        self.daily_pnl[asset_class] += pnl

        if self.starting_equity_today is None:
            # safety net: if nobody set a starting point, treat now as the baseline
            self.starting_equity_today = total_account_equity
            return

        total_pnl_pct = (total_account_equity - self.starting_equity_today) / self.starting_equity_today
        account_limit = self.config["account_wide"]["max_daily_loss_pct"]

        if total_pnl_pct <= -abs(account_limit):
            self.account_halted = True

    def reset_daily(self, total_equity_now=None):
        self.daily_pnl = {k: 0.0 for k in self.asset_classes}
        self.trading_halted = {k: False for k in self.asset_classes}
        self.account_halted = False
        self.starting_equity_today = total_equity_now
