// GitHub-contribution-style calendar heatmap of daily net P&L, built
// from the live trade log's per-trade `pnl` + `time` fields (full ISO
// datetimes as of the trade-log date fix -- see server.py). Grouped by
// calendar day, colored by win/loss and scaled by magnitude relative to
// the biggest single day in the visible window.
function dayKey(d) {
  return d.toISOString().slice(0, 10);
}

export function CalendarHeatmap({ trades, weeks = 14 }) {
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
  const start = new Date(today);
  start.setDate(start.getDate() - start.getDay() - (weeks - 1) * 7);

  const maxAbs = Math.max(1, ...Object.values(dailyPnl).map((v) => Math.abs(v)));

  const cols = [];
  for (let w = 0; w < weeks; w++) {
    const col = [];
    for (let d = 0; d < 7; d++) {
      const day = new Date(start);
      day.setDate(day.getDate() + w * 7 + d);
      const key = dayKey(day);
      const pnl = dailyPnl[key];
      const isFuture = day > today;
      let level;
      if (!isFuture && pnl != null) {
        const ratio = Math.abs(pnl) / maxAbs;
        const tier = ratio > 0.66 ? 3 : ratio > 0.33 ? 2 : 1;
        level = `${pnl >= 0 ? 'win' : 'loss'}-${tier}`;
      }
      col.push({ key, pnl, level, isFuture, day });
    }
    cols.push(col);
  }

  return (
    <div>
      <div className="heatmap-wrap">
        {cols.map((col, i) => (
          <div className="heatmap-col" key={i}>
            {col.map((cell) => (
              <div
                key={cell.key}
                className="heatmap-cell"
                data-level={cell.level}
                style={cell.isFuture ? { visibility: 'hidden' } : undefined}
                title={
                  cell.isFuture
                    ? ''
                    : cell.pnl != null
                      ? `${cell.key}: ${cell.pnl >= 0 ? '+' : ''}$${cell.pnl.toFixed(2)}`
                      : `${cell.key}: no trades`
                }
              />
            ))}
          </div>
        ))}
      </div>
      <div className="heatmap-legend">
        <span>Loss</span>
        <div className="heatmap-cell" data-level="loss-3" />
        <div className="heatmap-cell" data-level="loss-1" />
        <div className="heatmap-cell" />
        <div className="heatmap-cell" data-level="win-1" />
        <div className="heatmap-cell" data-level="win-3" />
        <span>Win</span>
      </div>
    </div>
  );
}
