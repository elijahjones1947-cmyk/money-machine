import { useDashboard } from '../hooks/useDashboard.js';

// Reuses /api/dashboard's `trades` array -- every trade already carries
// an `explanation` string (trade_explanations.py, generated at execution
// time and persisted alongside the trade -- see db.py's trades.explanation
// column). This widget is purely a new SURFACE for that existing data: a
// lightweight feed of the most recent trades and why each one happened,
// without needing to click into the Trade Log page and expand rows one
// at a time.
export function TradeRationaleWidget() {
  const { data, loading } = useDashboard();
  const trades = data?.trades ?? [];

  if (loading) return <div className="empty-state">Loading…</div>;
  if (trades.length === 0) return <div className="empty-state">No trades yet</div>;

  const recent = trades.slice(-5).reverse();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {recent.map((t, i) => (
        <div key={i} style={{ borderBottom: i < recent.length - 1 ? '1px solid var(--border)' : 'none', paddingBottom: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 2 }}>
            <span style={{ fontWeight: 600 }}>
              {t.action === 'buy' ? '▲' : '▼'} {t.symbol}
            </span>
            <span style={{ color: 'var(--text-secondary)' }}>
              {t.time ? new Date(t.time).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}
            </span>
          </div>
          <div style={{
            fontSize: 12, color: 'var(--text-secondary)', overflow: 'hidden',
            textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
          }}>
            {t.explanation || 'No rationale recorded for this trade.'}
          </div>
        </div>
      ))}
    </div>
  );
}
