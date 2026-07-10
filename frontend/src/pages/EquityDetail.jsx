import { useMemo } from 'react';
import { useDashboard } from '../hooks/useDashboard.js';
import { EquityCurveChart } from '../components/EquityCurveChart.jsx';
import { dailyPnlByAssetClass } from '../utils/equityByClass.js';

const ASSET_CLASS_COLORS = { stock: '#7ab8ff', forex: '#39ff8f', crypto: '#ffce54' };
const ASSET_CLASS_LABELS = { stock: 'Stock', forex: 'Forex', crypto: 'Crypto' };

export function EquityDetail() {
  const { data, loading } = useDashboard();

  const times = data?.equity_history?.times ?? [];
  const values = data?.equity_history?.values ?? [];
  const equity = data?.combined_equity;
  const startToday = data?.risk_state?.starting_equity_today;
  const change = startToday != null && equity != null ? equity - startToday : null;
  const changePct = change != null && startToday ? (change / startToday) * 100 : null;
  const points = times.map((t, i) => ({ time: t, equity: values[i] }));

  const allTimeLow = values.length ? Math.min(...values) : null;
  const allTimeHigh = values.length ? Math.max(...values) : null;

  const trades = data?.trades ?? [];
  const positions = data?.positions ?? [];
  const byClass = useMemo(() => dailyPnlByAssetClass(trades, positions), [trades, positions]);
  const byClassSeries = Object.entries(byClass)
    .filter(([, pts]) => pts.some((p) => p.equity !== 0))
    .map(([ac, pts]) => ({ name: ASSET_CLASS_LABELS[ac], color: ASSET_CLASS_COLORS[ac], points: pts }));

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Equity</h1>
          <div className="page-subtitle">Combined account value across Alpaca (stock/crypto) and OANDA (forex), sampled periodically.</div>
        </div>
      </div>

      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : (
        <>
          <div className="stat-grid section">
            <div className="stat-card">
              <span className="metric-label">Current</span>
              <span className="metric-value">{equity != null ? `$${equity.toLocaleString()}` : '—'}</span>
            </div>
            <div className="stat-card">
              <span className="metric-label">Today</span>
              <span className={`metric-value ${change >= 0 ? 'positive' : 'negative'}`}>
                {change != null ? `${change >= 0 ? '+' : ''}$${change.toFixed(2)}` : '—'}
              </span>
            </div>
            <div className="stat-card">
              <span className="metric-label">Today %</span>
              <span className={`metric-value ${changePct >= 0 ? 'positive' : 'negative'}`}>
                {changePct != null ? `${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)}%` : '—'}
              </span>
            </div>
            <div className="stat-card">
              <span className="metric-label">Window high</span>
              <span className="metric-value">{allTimeHigh != null ? `$${allTimeHigh.toLocaleString()}` : '—'}</span>
            </div>
            <div className="stat-card">
              <span className="metric-label">Window low</span>
              <span className="metric-value">{allTimeLow != null ? `$${allTimeLow.toLocaleString()}` : '—'}</span>
            </div>
          </div>

          <div className="card section">
            {points.length > 1 ? (
              <EquityCurveChart
                height={280}
                series={[{ name: 'Equity', color: 'var(--accent)', points }]}
                showArea
                showLabels
              />
            ) : (
              <div className="empty-state">Not enough equity history yet — this fills in as the bot runs.</div>
            )}
          </div>

          <div className="section">
            <div className="section-title">Daily P&amp;L by asset class</div>
            <div className="card">
              <div className="page-subtitle" style={{ marginBottom: 12 }}>
                Cumulative realized P&amp;L per day, plus today's open unrealized P&amp;L folded into the latest
                point. Not a separate account balance — stock and crypto share one Alpaca account, so this tracks
                each asset class's contribution to overall equity rather than an independent balance.
              </div>
              {byClassSeries.length > 0 ? (
                <>
                  <EquityCurveChart height={220} series={byClassSeries} showLabels />
                  <div className="chart-legend">
                    {byClassSeries.map((s) => (
                      <span key={s.name}><span className="legend-dot" style={{ background: s.color }} />{s.name}</span>
                    ))}
                  </div>
                </>
              ) : (
                <div className="empty-state">No closed trades yet to break out by asset class.</div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
