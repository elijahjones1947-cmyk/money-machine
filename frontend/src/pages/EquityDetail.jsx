import { useDashboard } from '../hooks/useDashboard.js';
import { EquityCurveChart } from '../components/EquityCurveChart.jsx';

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
        </>
      )}
    </div>
  );
}
