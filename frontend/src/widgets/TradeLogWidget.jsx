import { useDashboard } from '../hooks/useDashboard.js';

export function TradeLogWidget() {
  const { data, loading } = useDashboard();
  const trades = data?.trades ?? [];
  const stats = data?.trade_stats;

  if (loading) return <div className="empty-state">Loading…</div>;

  return (
    <div className="metric">
      <span className="metric-label">Win rate</span>
      <span className="metric-value">{stats ? `${stats.win_rate}%` : '—'}</span>
      {trades.length === 0 ? (
        <div className="empty-state">No trades yet</div>
      ) : (
        <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {[...trades].slice(-4).reverse().map((t, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
              <span>{t.action?.toUpperCase()} {t.symbol}</span>
              <span style={{ color: 'var(--text-secondary)' }}>{t.time}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
