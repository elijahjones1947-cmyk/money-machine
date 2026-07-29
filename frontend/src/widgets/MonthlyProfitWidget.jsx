import { useDashboard } from '../hooks/useDashboard.js';

// "The rent generator" -- month-to-date REALIZED P&L only (server-side:
// api_dashboard()'s `completed` list, same pnl-is-not-null filter every
// other trade stat on the dashboard already uses), never the equity
// curve or an open position's unrealized P&L. Resets naturally each
// calendar month since the backend just re-filters trade_log by the
// current month on every call -- nothing to reset here client-side.
export function MonthlyProfitWidget() {
  const { data, loading } = useDashboard();
  const mp = data?.monthly_profit;

  if (loading) return <div className="empty-state">Loading…</div>;
  if (!mp) return <div className="empty-state">No data</div>;

  const pnl = mp.realized_pnl;
  const goal = mp.goal;
  const rawPct = mp.pct_of_goal ?? 0;
  const barPct = Math.max(0, Math.min(100, rawPct));
  const negative = pnl < 0;
  const goalHit = goal > 0 && pnl >= goal;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div className="metric">
        <span className="metric-label">{mp.month} realized P&amp;L</span>
        <span className={`metric-value ${negative ? 'negative' : 'positive'}`} style={{ fontSize: 30 }}>
          {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
        </span>
      </div>

      <div style={{
        position: 'relative', height: 14, borderRadius: 7,
        background: 'var(--bg-elevated)', border: '1px solid var(--border)', overflow: 'hidden',
      }}>
        <div style={{
          position: 'absolute', top: 0, bottom: 0, left: 0, width: `${barPct}%`,
          background: negative ? 'var(--danger)' : 'var(--accent)',
          borderRadius: 7, transition: 'width 0.3s ease',
        }} />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, color: 'var(--text-secondary)' }}>
        <span>{rawPct.toFixed(0)}% of ${goal.toFixed(0)} goal</span>
        {goalHit && <span style={{ color: 'var(--accent)', fontWeight: 700 }}>🎉 goal hit</span>}
      </div>
    </div>
  );
}
