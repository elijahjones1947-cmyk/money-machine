import { useMemo } from 'react';
import { useDashboard } from '../hooks/useDashboard.js';
import { DailyPnlSummary } from '../components/DailyPnlSummary.jsx';
import '../widgets/MonthlyProfitWidget.css';

const PACE_LABELS = {
  goal_hit: '🎉 Goal hit',
  on_track: 'On track',
  behind_pace: '⚠ Behind pace',
};

function monthKey(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

function monthLabel(key) {
  const [y, m] = key.split('-').map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
}

// The full pace picture behind the "rent generator" widget: this
// month's stats + pace status, a day-by-day breakdown of the CURRENT
// month specifically (DailyPnlSummary's `days` window sized to exactly
// how many days have elapsed), and a past-months table. Past months are
// computed client-side from the same full trade history the Trade Log
// page uses -- api_dashboard() only tracks the CURRENT month server-
// side, so there's no historical monthly_profit_goal to compare against
// (see the caveat rendered with the table below).
export function MonthlyProfitDetail() {
  const { data, loading } = useDashboard();
  const mp = data?.monthly_profit;
  const trades = data?.trades ?? [];

  const pastMonths = useMemo(() => {
    const byMonth = new Map();
    for (const t of trades) {
      if (t.pnl == null) continue;
      const key = monthKey(t.time);
      if (!key || (mp && key === mp.month)) continue;
      byMonth.set(key, (byMonth.get(key) || 0) + t.pnl);
    }
    return [...byMonth.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1));
  }, [trades, mp]);

  if (loading || !mp) {
    return (
      <div>
        <div className="page-header"><div><h1>Monthly profit</h1></div></div>
        <div className="empty-state">Loading…</div>
      </div>
    );
  }

  const negative = mp.realized_pnl < 0;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Monthly profit</h1>
          <div className="page-subtitle">
            The "rent generator" -- month-to-date REALIZED P&amp;L (closed trades only, not open-position P&amp;L or
            the equity curve) against the goal set in Settings, and whether current pace will actually hit it.
          </div>
        </div>
      </div>

      <div className="stat-grid section">
        <div className="stat-card">
          <span className="metric-label">{mp.month} realized P&amp;L</span>
          <span className={`metric-value ${negative ? 'negative' : 'positive'}`}>
            {mp.realized_pnl >= 0 ? '+' : ''}${mp.realized_pnl.toFixed(2)}
          </span>
        </div>
        <div className="stat-card">
          <span className="metric-label">Goal</span>
          <span className="metric-value">${mp.goal.toFixed(0)}</span>
        </div>
        <div className="stat-card">
          <span className="metric-label">% of goal</span>
          <span className="metric-value">{mp.pct_of_goal != null ? `${mp.pct_of_goal}%` : '—'}</span>
        </div>
        <div className="stat-card">
          <span className="metric-label">Days left this month</span>
          <span className="metric-value">{mp.days_remaining_in_month}</span>
        </div>
        <div className="stat-card">
          <span className="metric-label">Current pace</span>
          <span className="metric-value">${mp.current_daily_pace.toFixed(2)}/day</span>
        </div>
        <div className="stat-card">
          <span className="metric-label">Required pace</span>
          <span className="metric-value">
            {mp.required_daily_pace != null ? `$${mp.required_daily_pace.toFixed(2)}/day` : '—'}
          </span>
        </div>
      </div>

      {mp.pace_status && (
        <div className="section">
          <span className={`pace-pill ${mp.pace_status}`} style={{ fontSize: 13, padding: '6px 14px' }}>
            {PACE_LABELS[mp.pace_status]}
          </span>
        </div>
      )}

      <div className="section">
        <div className="section-title">Day by day, this month</div>
        <div className="card">
          <DailyPnlSummary trades={trades} days={mp.days_elapsed_in_month} />
        </div>
      </div>

      <div className="section">
        <div className="section-title">Past months</div>
        <div className="page-subtitle" style={{ marginBottom: 12 }}>
          Compared against the CURRENT goal (${mp.goal.toFixed(0)}) -- if the goal has changed since a given month,
          that month's "hit goal" call reflects today's target, not whatever it was set to at the time.
        </div>
        {pastMonths.length === 0 ? (
          <div className="card"><div className="empty-state">No prior months with closed trades yet</div></div>
        ) : (
          <div className="table-card">
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr><th>Month</th><th>Realized P&amp;L</th><th>% of goal</th><th>Hit goal?</th></tr>
                </thead>
                <tbody>
                  {pastMonths.map(([key, pnl]) => (
                    <tr key={key}>
                      <td>{monthLabel(key)}</td>
                      <td style={{ color: pnl >= 0 ? 'var(--accent)' : 'var(--danger)', fontWeight: 600 }}>
                        {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                      </td>
                      <td>{mp.goal ? `${((pnl / mp.goal) * 100).toFixed(0)}%` : '—'}</td>
                      <td>{mp.goal && pnl >= mp.goal ? '✅' : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
