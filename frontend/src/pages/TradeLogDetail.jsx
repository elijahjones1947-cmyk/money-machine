import { useDashboard } from '../hooks/useDashboard.js';

export function TradeLogDetail() {
  const { data, loading } = useDashboard();
  const trades = data?.trades ?? [];
  const stats = data?.trade_stats;

  return (
    <div>
      <h2>Trade log</h2>
      {stats && (
        <div style={{ display: 'flex', gap: 24, marginBottom: 20, flexWrap: 'wrap' }}>
          <div className="metric"><span className="metric-label">Win rate</span><span className="metric-value" style={{ fontSize: 24 }}>{stats.win_rate}%</span></div>
          <div className="metric"><span className="metric-label">Avg gain</span><span className="metric-value positive" style={{ fontSize: 24 }}>${stats.avg_gain}</span></div>
          <div className="metric"><span className="metric-label">Avg loss</span><span className="metric-value negative" style={{ fontSize: 24 }}>${stats.avg_loss}</span></div>
          <div className="metric"><span className="metric-label">Best trade</span><span className="metric-value positive" style={{ fontSize: 24 }}>${stats.best_trade}</span></div>
          <div className="metric"><span className="metric-label">Worst trade</span><span className="metric-value negative" style={{ fontSize: 24 }}>${stats.worst_trade}</span></div>
        </div>
      )}
      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : trades.length === 0 ? (
        <div className="empty-state">No trades yet</div>
      ) : (
        <table className="data-table">
          <thead>
            <tr><th>Time</th><th>Action</th><th>Symbol</th><th>Asset class</th><th>Qty</th><th>Price</th><th>P&amp;L</th><th>Regime</th></tr>
          </thead>
          <tbody>
            {[...trades].reverse().map((t, i) => (
              <tr key={i}>
                <td>{t.time}</td>
                <td>{t.action?.toUpperCase()}</td>
                <td>{t.symbol}</td>
                <td>{t.asset_class}</td>
                <td>{t.qty}</td>
                <td>{t.price}</td>
                <td style={{ color: t.pnl == null ? 'var(--text-muted)' : t.pnl >= 0 ? 'var(--accent)' : 'var(--danger)' }}>
                  {t.pnl == null ? '—' : `${t.pnl >= 0 ? '+' : ''}${t.pnl}`}
                </td>
                <td>{t.regime || 'unknown'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
