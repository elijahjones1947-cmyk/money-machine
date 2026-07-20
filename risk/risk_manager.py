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

    def __init__(self, config, dust_position_value_usd=5.0):
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
        # See check_trade's open_position_values param -- passed in here
        # (not imported from config.py directly) to keep this class
        # broker/config-module-agnostic, same reasoning as `config` above
        # being injected rather than imported.
        self.dust_position_value_usd = dust_position_value_usd

    def set_starting_equity(self, total_equity):
        """Call once per day (e.g. on first trade or a daily reset job)."""
        self.starting_equity_today = total_equity

    def check_trade(self, broker, symbol, side, size, asset_class, price=None, reduces_position=False,
                     open_position_values=None):
        """Returns (approved: bool, reason: str).

        `price` should be the SAME price snapshot the caller used to size
        the trade. If omitted, we fetch our own — but for fast-moving
        assets (crypto especially) that can cause a price-drift mismatch
        where a correctly-sized trade gets rejected because the price
        ticked between sizing and validation. Always pass price when you
        have it.

        `reduces_position` should be True when this trade strictly
        reduces or closes existing exposure (a sell sized to <= what's
        currently held long, or a buy sized to <= what's currently held
        short) rather than opening or adding to a position. Every check
        in here exists to prevent taking on MORE risk -- none of them
        should ever block a trade that's making risk smaller, and
        letting them do so is actively dangerous: it's exactly the
        scenario where a halt (hit right after a big loss) would trap
        you in a losing position you're trying to exit. Bypassing ALL
        checks (including the account-wide and per-asset-class halts)
        for a genuinely risk-reducing trade is intentional, not an
        oversight -- see the position safety-net monitor in server.py,
        which depends on this to be able to force-close a position even
        when trading is halted.

        `open_position_values`, when given, is the list of notional USD
        values (one per currently open position in THIS asset class) --
        used ONLY for the max_open_positions count below, so a position
        worth less than dust_position_value_usd (a rounding leftover
        from an imprecise fill/close, not real capital at risk) never
        occupies a cap "slot". This class deliberately doesn't know how
        to compute a position's value itself (that requires broker-
        specific field parsing -- see server.py's get_all_positions(),
        which is what actually builds this list), so it stays
        broker-agnostic exactly like `config` above. Omit it (the
        default) to fall back to the plain broker.get_positions() count
        -- unchanged behavior for any caller that doesn't have a
        normalized position list handy.
        """
        if reduces_position:
            return True, "OK (reduces/closes existing position -- risk checks don't apply)"

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
        if price is None:
            price = broker.get_price(symbol)
        position_value = float(size) * price
        max_allowed = account["equity"] * rules["max_position_size_pct"]
        if position_value > max_allowed:
            return False, "Position too big: ${:.2f} > max ${:.2f}".format(position_value, max_allowed)

        # max open positions -- dust excluded from the count (see
        # open_position_values's docstring above), everything else
        # unchanged.
        if open_position_values is not None:
            open_position_count = sum(1 for v in open_position_values if v >= self.dust_position_value_usd)
        else:
            open_position_count = len(broker.get_positions())
        if open_position_count >= rules["max_open_positions"]:
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
