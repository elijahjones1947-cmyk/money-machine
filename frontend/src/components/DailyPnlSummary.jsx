// Simple daily P&L strip: today's number up front, then a single row of
// bars for the last N days -- replaces the old GitHub-contribution-style
// calendar grid, which buried "what did we make today" under a 14-week
// heatmap + tiered legend most people found hard to parse at a glance.
function dayKey(d) {
  return d.toISOString().slice(0, 10);
}

export function DailyPnlSummary({ trades, days = 14 }) {
  const dailyPnl = {};
  for (const t of trades) {
    if (t.pnl == null) continue;
    const d = new Date(t.time);
    if (isNaN(d.getTime())) continue;
    const key = dayKey(d);
    dailyPnl[key] = (dailyPnl[key] || 0) + t.pnl;
  }

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const cells = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const key = dayKey(d);
    cells.push({ key, day: d, pnl: dailyPnl[key] });
  }

  const todayPnl = dailyPnl[dayKey(today)] ?? 0;
  const maxAbs = Math.max(1, ...cells.map((c) => Math.abs(c.pnl || 0)));

  return (
    <div>
      <div className="metric" style={{ marginBottom: 14 }}>
        <span className="metric-label">Today</span>
        <span className={`metric-value ${todayPnl >= 0 ? 'positive' : 'negative'}`} style={{ fontSize: 30 }}>
          {todayPnl >= 0 ? '+' : ''}${todayPnl.toFixed(2)}
        </span>
      </div>
      <div className="daily-pnl-strip">
        {cells.map((c) => {
          const hasData = c.pnl != null;
          const heightPct = hasData ? Math.max(8, (Math.abs(c.pnl) / maxAbs) * 100) : 8;
          return (
            <div key={c.key} className="daily-pnl-bar-col" title={hasData ? `${c.key}: ${c.pnl >= 0 ? '+' : ''}$${c.pnl.toFixed(2)}` : `${c.key}: no trades`}>
              <div className="daily-pnl-bar-track">
                <div
                  className="daily-pnl-bar-fill"
                  data-state={!hasData ? 'empty' : c.pnl >= 0 ? 'win' : 'loss'}
                  style={{ height: `${heightPct}%` }}
                />
              </div>
              <span className="daily-pnl-bar-label">{c.day.toLocaleDateString(undefined, { weekday: 'narrow' })}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
