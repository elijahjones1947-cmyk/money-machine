import { Fragment, useMemo, useState } from 'react';
import { useDashboard } from '../hooks/useDashboard.js';
import { BarBreakdownChart } from '../components/BarBreakdownChart.jsx';
import { DailyPnlSummary } from '../components/DailyPnlSummary.jsx';

const SOURCE_BADGES = {
  safety_stop_loss: { label: 'Safety net', title: 'Force-closed by the independent safety-net monitor, not a normal strategy exit' },
  manual: { label: 'Manual', title: 'Placed directly via the dashboard’s manual buy/sell buttons' },
  manual_close: { label: 'Manual close', title: 'Closed directly via the dashboard’s per-position Close button' },
  strategy_switch: { label: 'Strategy switch', title: 'Force-closed because the symbol’s active strategy was reassigned' },
};

const REGIME_COLOR = {
  trending: 'var(--regime-trending)',
  choppy: 'var(--regime-choppy)',
  volatile: 'var(--regime-volatile)',
  unknown: 'var(--regime-unknown)',
};

const ASSET_CLASSES = ['stock', 'forex', 'crypto'];

function SourceBadge({ source }) {
  const info = SOURCE_BADGES[source];
  if (!info) return <span className="regime-badge">Strategy</span>;
  const isForcedExit = source === 'safety_stop_loss' || source === 'strategy_switch';
  return (
    <span className={isForcedExit ? 'action-badge sell' : 'regime-badge'} title={info.title}>
      {info.label}
    </span>
  );
}

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

function isToday(isoTime) {
  const d = new Date(isoTime);
  if (isNaN(d.getTime())) return false;
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
}

export function TradeLogDetail() {
  const { data, loading } = useDashboard();
  const trades = data?.trades ?? [];
  const stats = data?.trade_stats;
  const watched = data?.watched_symbols ?? {};
  const [expandedIndex, setExpandedIndex] = useState(null);
  const [classFilter, setClassFilter] = useState('all');
  const [symbolFilter, setSymbolFilter] = useState('');

  const byRegime = useMemo(() => breakdownBy(trades, (t) => t.regime), [trades]);
  const bySymbol = useMemo(() => breakdownBy(trades, (t) => t.symbol), [trades]);
  const reversedTrades = useMemo(() => [...trades].reverse(), [trades]);

  const todaysClosed = useMemo(() => trades.filter((t) => t.pnl != null && isToday(t.time)), [trades]);
  const todaysClosedPnl = todaysClosed.reduce((sum, t) => sum + t.pnl, 0);

  const filteredTrades = useMemo(() => {
    return reversedTrades.filter((t) => {
      if (classFilter !== 'all' && t.asset_class !== classFilter) return false;
      if (symbolFilter && !t.symbol?.toLowerCase().includes(symbolFilter.toLowerCase())) return false;
      return true;
    });
  }, [reversedTrades, classFilter, symbolFilter]);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Trade history</h1>
          <div className="page-subtitle">Every executed trade, plus win/loss patterns by regime and symbol and a daily P&amp;L summary.</div>
        </div>
      </div>

      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : (
        <>
          <div className="stat-grid section">
            <div className="stat-card">
              <span className="metric-label">Closed today</span>
              <span className="metric-value">{todaysClosed.length}</span>
            </div>
            <div className="stat-card">
              <span className="metric-label">Today's closed P&amp;L</span>
              <span className={`metric-value ${todaysClosedPnl >= 0 ? 'positive' : 'negative'}`}>
                {todaysClosedPnl >= 0 ? '+' : ''}${todaysClosedPnl.toFixed(2)}
              </span>
            </div>
            {stats && (
              <>
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
              </>
            )}
          </div>

          <div className="section">
            <div className="section-title">Currently trading</div>
            <div className="card" style={{ display: 'flex', flexWrap: 'wrap', gap: 20 }}>
              {ASSET_CLASSES.map((ac) => (
                <div key={ac} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ fontSize: 12, fontWeight: 700, textTransform: 'capitalize', color: 'var(--text-secondary)' }}>{ac}</span>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {(watched[ac] || []).length === 0
                      ? <span className="empty-state" style={{ padding: 0 }}>none watched</span>
                      : (watched[ac] || []).map((s) => <span key={s} className="pill">{s}</span>)}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="section">
            <div className="section-title">Daily P&amp;L</div>
            <div className="card">
              <DailyPnlSummary trades={trades} />
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
              <>
                <div className="trade-log-filters">
                  <div className="trade-log-filter-tabs">
                    <button className={`button ${classFilter === 'all' ? 'button-accent' : ''}`} onClick={() => setClassFilter('all')}>All</button>
                    {ASSET_CLASSES.map((ac) => (
                      <button key={ac} className={`button ${classFilter === ac ? 'button-accent' : ''}`} style={{ textTransform: 'capitalize' }} onClick={() => setClassFilter(ac)}>
                        {ac}
                      </button>
                    ))}
                  </div>
                  <input
                    value={symbolFilter}
                    onChange={(e) => setSymbolFilter(e.target.value)}
                    placeholder="Filter by symbol…"
                    className="trade-log-symbol-filter"
                  />
                </div>
                <div className="table-card">
                  <div className="table-scroll">
                    <table className="data-table">
                      <thead>
                        <tr><th>Time</th><th>Trade</th><th>Qty</th><th>Price</th><th>P&amp;L</th><th>Regime</th><th>Source</th><th>Why</th></tr>
                      </thead>
                      <tbody>
                        {filteredTrades.length === 0 ? (
                          <tr><td colSpan={8}><div className="empty-state">No trades match this filter</div></td></tr>
                        ) : filteredTrades.map((t, i) => (
                          <Fragment key={i}>
                            <tr
                              onClick={() => t.explanation && setExpandedIndex(expandedIndex === i ? null : i)}
                              style={{
                                ...(t.source === 'safety_stop_loss' ? { background: 'var(--danger-dim)' } : undefined),
                                cursor: t.explanation ? 'pointer' : 'default',
                              }}
                            >
                              <td style={{ whiteSpace: 'nowrap' }}>{new Date(t.time).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</td>
                              <td>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                  <span className={`action-badge ${t.action === 'sell' ? 'sell' : 'buy'}`}>{t.action}</span>
                                  <span style={{ fontWeight: 700 }}>{t.symbol}</span>
                                  <span style={{ fontSize: 11, color: 'var(--text-muted)', textTransform: 'capitalize' }}>{t.asset_class}</span>
                                </div>
                              </td>
                              <td>{t.qty}</td>
                              <td>{t.price}</td>
                              <td style={{ color: t.pnl == null ? 'var(--text-muted)' : t.pnl >= 0 ? 'var(--accent)' : 'var(--danger)', fontWeight: 600 }}>
                                {t.pnl == null ? '—' : `${t.pnl >= 0 ? '+' : ''}${t.pnl}`}
                              </td>
                              <td>
                                <span className="regime-badge" style={{ borderColor: REGIME_COLOR[t.regime || 'unknown'], color: REGIME_COLOR[t.regime || 'unknown'] }}>
                                  {t.regime || 'unknown'}
                                </span>
                              </td>
                              <td><SourceBadge source={t.source} /></td>
                              <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                                {t.explanation ? (expandedIndex === i ? '▲ hide' : '▼ show') : '—'}
                              </td>
                            </tr>
                            {expandedIndex === i && t.explanation && (
                              <tr>
                                <td colSpan={8} style={{ background: 'var(--bg)', fontSize: 13, color: 'var(--text-secondary)', padding: '10px 12px' }}>
                                  {t.explanation}
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}
