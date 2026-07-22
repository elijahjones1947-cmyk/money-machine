import { useDashboard } from '../hooks/useDashboard.js';
import { EquityCurveChart } from '../components/EquityCurveChart.jsx';

// Live equity curve widget -- reuses state.equity_history (periodic
// snapshots of combined_equity across both brokers, persisted to
// Postgres) rather than reconstructing a curve from trade P&L, since
// the persisted series already reflects mark-to-market on open
// positions, not just realized sells.
export function EquityWidget() {
  const { data, loading } = useDashboard();
  if (loading) return <div className="empty-state">Loading…</div>;

  const times = data?.equity_history?.times ?? [];
  const values = data?.equity_history?.values ?? [];
  const equity = data?.combined_equity;
  const startToday = data?.risk_state?.starting_equity_today;
  const change = startToday != null && equity != null ? equity - startToday : null;
  const changePct = change != null && startToday ? (change / startToday) * 100 : null;

  const points = times.map((t, i) => ({ time: t, equity: values[i] }));

  return (
    <div className="metric">
      <span className="metric-label">Total mark</span>
      <span className="metric-value">{equity != null ? `$${equity.toLocaleString()}` : '—'}</span>
      {change != null && (
        <span className={change >= 0 ? 'metric-value positive' : 'metric-value negative'} style={{ fontSize: 13, fontWeight: 600 }}>
          {change >= 0 ? '+' : ''}${change.toFixed(2)} today ({changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}%)
        </span>
      )}
      <div style={{ marginTop: 8, height: 56 }}>
        {points.length > 1 ? (
          <EquityCurveChart height={56} series={[{ name: 'Equity', color: 'var(--accent)', points }]} showArea />
        ) : (
          <div className="empty-state" style={{ padding: 0 }}>Collecting data…</div>
        )}
      </div>
    </div>
  );
}
