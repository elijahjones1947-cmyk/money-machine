import { useDashboard } from '../hooks/useDashboard.js';
import './MonthlyProfitWidget.css';

// "The rent generator" -- month-to-date REALIZED P&L only (server-side:
// api_dashboard()'s `completed` list, same pnl-is-not-null filter every
// other trade stat on the dashboard already uses), never the equity
// curve or an open position's unrealized P&L. Resets naturally each
// calendar month since the backend just re-filters trade_log by the
// current month on every call -- nothing to reset here client-side.
//
// Pace fields (days_elapsed/remaining_in_month, current/required_daily_pace,
// pace_status) are computed server-side in server.py's api_dashboard() --
// see the comment there for the days_elapsed/days_remaining overlap-by-
// design rationale. This widget just renders whatever pace_status says.
const PACE_LABELS = {
  goal_hit: '🎉 Goal hit',
  on_track: 'On track',
  behind_pace: '⚠ Behind pace',
};

// "Urgent" (pulsing) once behind pace with a week or less left in the
// month -- the whole point of this feature is that the widget should
// visibly nag as the runway shrinks, not just sit at a static red pill
// all month.
const URGENT_DAYS_REMAINING_THRESHOLD = 7;

export function MonthlyProfitWidget() {
  const { data, loading } = useDashboard();
  const mp = data?.monthly_profit;

  if (loading) return <div className="empty-state">Loading…</div>;
  if (!mp) return <div className="empty-state">No data</div>;

  const pnl = mp.realized_pnl;
  const goal = mp.goal;
  const rawPct = mp.pct_of_goal ?? 0;
  const barPct = Math.max(0, Math.min(100, rawPct));
  const negative = pnl < 0;
  const paceStatus = mp.pace_status;
  const daysRemaining = mp.days_remaining_in_month;
  const urgent = paceStatus === 'behind_pace' && daysRemaining != null && daysRemaining <= URGENT_DAYS_REMAINING_THRESHOLD;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div className="metric">
        <span className="metric-label">{mp.month} realized P&amp;L</span>
        <span className={`metric-value ${negative ? 'negative' : 'positive'}`} style={{ fontSize: 30 }}>
          {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
        </span>
      </div>

      <div style={{
        position: 'relative', height: 14, borderRadius: 7,
        background: 'var(--bg-elevated)', border: '1px solid var(--border)', overflow: 'hidden',
      }}>
        <div style={{
          position: 'absolute', top: 0, bottom: 0, left: 0, width: `${barPct}%`,
          background: negative ? 'var(--danger)' : 'var(--accent)',
          borderRadius: 7, transition: 'width 0.3s ease',
        }} />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, color: 'var(--text-secondary)' }}>
        <span>{rawPct.toFixed(0)}% of ${goal.toFixed(0)} goal</span>
        {paceStatus && <span className={`pace-pill ${paceStatus} ${urgent ? 'urgent' : ''}`}>{PACE_LABELS[paceStatus]}</span>}
      </div>

      {paceStatus && paceStatus !== 'goal_hit' && (
        <div style={{ fontSize: 12, color: urgent ? 'var(--danger)' : 'var(--text-secondary)' }}>
          {daysRemaining} day{daysRemaining === 1 ? '' : 's'} left · need <strong>${mp.required_daily_pace.toFixed(2)}/day</strong> to
          hit goal (averaging ${mp.current_daily_pace.toFixed(2)}/day so far)
        </div>
      )}
    </div>
  );
}
