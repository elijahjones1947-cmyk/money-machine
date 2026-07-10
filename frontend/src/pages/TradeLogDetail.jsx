import { useMemo } from 'react';
import { useDashboard } from '../hooks/useDashboard.js';
import { BarBreakdownChart } from '../components/BarBreakdownChart.jsx';
import { CalendarHeatmap } from '../components/CalendarHeatmap.jsx';

function breakdownBy(trades, keyFn) {
  const map = new Map();
  for (const t of trades) {
    if (t.pnl == null) continue; // only completed (closed) trades count as a win/loss
    const key = keyFn(t) || 'unknown';
    if (!map.has(key)) map.set(key, { label: key, wins: 0, losses: 0 });
    const row = map.get(key);
    if (t.pnl > 0) row.wins += 1;
    else if (t.pnl < 0) row.losses += 1;
  }
  return [...map.values()].sort((a, b) => (b.wins + b.losses) - (a.wins + a.losses));
}

export function TradeLogDetail() {
  const { data, loading } = useDashboard();
  const trades = data?.trades ?? [];
  const stats = data?.trade_stats;

  const byRegime = useMemo(() => breakdownBy(trades, (t) => t.regime), [trades]);
  const bySymbol = useMemo(() => breakdownBy(trades, (t) => t.symbol), [trades]);
  const reversedTrades = useMemo(() => [...trades].reverse(), [trades]);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Trade history</h1>
          <div className="page-subtitle">Every executed trade, plus win/loss patterns by regime and symbol and a daily P&amp;L calendar.</div>
        </div>
      </div>

      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : (
        <>
          {stats && (
            <div className="stat-grid section">
              <div className="stat-card">
                <span className="metric-label">Win rate</span>
                <span className="metric-value">{stats.win_rate}%</span>
              </div>
              <div className="stat-card">
                <span className="metric-label">Avg gain</span>
                <span className="metric-value positive">${stats.avg_gain}</span>
              </div>
              <div className="stat-card">
                <span className="metric-label">Avg loss</span>
                <span className="metric-value negative">${stats.avg_loss}</span>
              </div>
              <div className="stat-card">
                <span className="metric-label">Best trade</span>
                <span className="metric-value positive">${stats.best_trade}</span>
              </div>
              <div className="stat-card">
                <span className="metric-label">Worst trade</span>
                <span className="metric-value negative">${stats.worst_trade}</span>
              </div>
            </div>
          )}

          <div className="section">
            <div className="section-title">Daily P&amp;L</div>
            <div className="card">
              <CalendarHeatmap trades={trades} />
            </div>
          </div>

          <div className="two-col-grid section">
            <div>
              <div className="section-title">Win / loss by regime</div>
              <div className="card">
                <BarBreakdownChart rows={byRegime} />
              </div>
            </div>
            <div>
              <div className="section-title">Win / loss by symbol</div>
              <div className="card">
                <BarBreakdownChart rows={bySymbol} />
              </div>
            </div>
          </div>

          <div className="section">
            <div className="section-title">All trades</div>
            {trades.length === 0 ? (
              <div className="card"><div className="empty-state">No trades yet</div></div>
            ) : (
              <div className="table-card">
                <div className="table-scroll">
                  <table className="data-table">
                    <thead>
                      <tr><th>Time</th><th>Action</th><th>Symbol</th><th>Asset class</th><th>Qty</th><th>Price</th><th>P&amp;L</th><th>Regime</th></tr>
                    </thead>
                    <tbody>
                      {reversedTrades.map((t, i) => (
                        <tr key={i}>
                          <td>{new Date(t.time).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</td>
                          <td><span className={`action-badge ${t.action === 'sell' ? 'sell' : 'buy'}`}>{t.action}</span></td>
                          <td>{t.symbol}</td>
                          <td>{t.asset_class}</td>
                          <td>{t.qty}</td>
                          <td>{t.price}</td>
                          <td style={{ color: t.pnl == null ? 'var(--text-muted)' : t.pnl >= 0 ? 'var(--accent)' : 'var(--danger)', fontWeight: 600 }}>
                            {t.pnl == null ? '—' : `${t.pnl >= 0 ? '+' : ''}${t.pnl}`}
                          </td>
                          <td><span className="regime-badge">{t.regime || 'unknown'}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
