// Diverging horizontal bar chart: wins extend right of center, losses
// extend left, both scaled to the same max so relative size is
// comparable across rows. Used for win/loss broken out by regime and
// by symbol on the Trade History page.
export function BarBreakdownChart({ rows }) {
  if (!rows || rows.length === 0) return <div className="empty-state">No completed trades yet</div>;

  const maxCount = Math.max(1, ...rows.map((r) => Math.max(r.wins, r.losses)));

  return (
    <div className="bar-breakdown">
      {rows.map((r) => (
        <div className="bar-breakdown-row" key={r.label}>
          <span className="bar-breakdown-label" title={r.label}>{r.label}</span>
          <div className="bar-breakdown-track">
            <div className="bar-breakdown-center" />
            <div className="bar-breakdown-fill loss" style={{ width: `${(r.losses / maxCount) * 50}%` }} />
            <div className="bar-breakdown-fill win" style={{ width: `${(r.wins / maxCount) * 50}%` }} />
          </div>
          <span className="bar-breakdown-value">{r.wins}W / {r.losses}L</span>
        </div>
      ))}
    </div>
  );
}
