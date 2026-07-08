import { useDashboard } from '../hooks/useDashboard.js';

export function PositionsWidget() {
  const { data, loading } = useDashboard();
  const positions = data?.positions ?? [];

  if (loading) return <div className="empty-state">Loading…</div>;
  if (positions.length === 0) return <div className="empty-state">No open positions</div>;

  return (
    <div className="metric">
      <span className="metric-label">Open positions</span>
      <span className="metric-value">{positions.length}</span>
      <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {positions.slice(0, 4).map((p, i) => (
          <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
            <span>{p.symbol}</span>
            <span className={p.unrealized_pl >= 0 ? 'metric-value positive' : 'metric-value negative'} style={{ fontSize: 13, fontWeight: 600 }}>
              {p.unrealized_pl >= 0 ? '+' : ''}{p.unrealized_pl}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
