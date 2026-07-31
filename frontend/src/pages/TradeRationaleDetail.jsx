import { useMemo, useState } from 'react';
import { useDashboard } from '../hooks/useDashboard.js';

// Full, untruncated rationale history -- distinct from Trade Log
// (which is about the trade itself: price/qty/P&L/regime/source) and
// from Monthly Profit (which is about the month's realized total).
// This page's only job is "why did each trade happen," in full, not
// the 5-trade/2-line-clamped preview the dashboard widget shows.
export function TradeRationaleDetail() {
  const { data, loading } = useDashboard();
  const trades = data?.trades ?? [];
  const [symbolFilter, setSymbolFilter] = useState('');

  const withRationale = useMemo(
    () => [...trades].reverse().filter((t) => t.explanation),
    [trades],
  );
  const filtered = useMemo(
    () => withRationale.filter((t) => !symbolFilter || t.symbol?.toLowerCase().includes(symbolFilter.toLowerCase())),
    [withRationale, symbolFilter],
  );

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Trade rationale</h1>
          <div className="page-subtitle">Full explanation for every trade that has one recorded, most recent first, no truncation.</div>
        </div>
      </div>

      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : withRationale.length === 0 ? (
        <div className="card"><div className="empty-state">No trade rationale recorded yet</div></div>
      ) : (
        <>
          <div className="section">
            <input
              value={symbolFilter}
              onChange={(e) => setSymbolFilter(e.target.value)}
              placeholder="Filter by symbol…"
              className="trade-log-symbol-filter"
            />
          </div>
          <div className="section">
            {filtered.length === 0 ? (
              <div className="card"><div className="empty-state">No trades match this filter</div></div>
            ) : filtered.map((t, i) => (
              <div className="card" style={{ marginBottom: 10 }} key={i}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className={`action-badge ${t.action === 'sell' ? 'sell' : 'buy'}`}>{t.action}</span>
                    <span style={{ fontWeight: 700 }}>{t.symbol}</span>
                    <span style={{ fontSize: 11, color: 'var(--text-muted)', textTransform: 'capitalize' }}>{t.asset_class}</span>
                  </div>
                  <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {t.time ? new Date(t.time).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}
                  </span>
                </div>
                <div style={{ fontSize: 13, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
                  {t.explanation}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
