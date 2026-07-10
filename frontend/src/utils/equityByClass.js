// Builds a per-asset-class "cumulative net P&L by day" series from the
// live trade log + open positions -- NOT a true separate account
// balance, since Alpaca shares one account/equity number across stock
// and crypto (see server.py's get_combined_equity docstring). This is
// the same shape of curve the backtester already produces per symbol
// (baseline + cumulative P&L), just grouped by asset class and by
// calendar day instead of by trade, and with today's open positions'
// unrealized P&L folded into the most recent point so it reflects
// where things actually stand right now, not just closed trades.
const ASSET_CLASSES = ['stock', 'forex', 'crypto'];

function dayKey(dateLike) {
  const d = new Date(dateLike);
  if (isNaN(d.getTime())) return null;
  return d.toISOString().slice(0, 10);
}

export function dailyPnlByAssetClass(trades, positions) {
  const realizedByClassDay = { stock: {}, forex: {}, crypto: {} };
  const days = new Set();

  for (const t of trades) {
    if (t.pnl == null) continue;
    const key = dayKey(t.time);
    if (!key) continue;
    days.add(key);
    const ac = ASSET_CLASSES.includes(t.asset_class) ? t.asset_class : null;
    if (!ac) continue;
    realizedByClassDay[ac][key] = (realizedByClassDay[ac][key] || 0) + t.pnl;
  }

  const todayKey = dayKey(new Date());
  days.add(todayKey);
  const sortedDays = [...days].sort();

  const unrealizedByClass = { stock: 0, forex: 0, crypto: 0 };
  for (const p of positions || []) {
    if (ASSET_CLASSES.includes(p.asset_class)) {
      unrealizedByClass[p.asset_class] += Number(p.unrealized_pl) || 0;
    }
  }

  const series = {};
  for (const ac of ASSET_CLASSES) {
    let running = 0;
    series[ac] = sortedDays.map((day) => {
      running += realizedByClassDay[ac][day] || 0;
      const value = day === todayKey ? running + unrealizedByClass[ac] : running;
      return { time: day, equity: value };
    });
  }
  return series;
}
