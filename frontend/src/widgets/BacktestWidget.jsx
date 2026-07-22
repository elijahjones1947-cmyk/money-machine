import { useBacktest } from '../hooks/useBacktest.js';
import { EquityCurveChart } from '../components/EquityCurveChart.jsx';

const CURVE_COLORS = ['#39ff8f', '#7ab8ff', '#ffce54'];

// Leads with LIVE performance (real trades) rather than the simulated
// backtest, since that's the more current/actionable number day to
// day -- the full backtest breakdown is one click away on the detail
// page. Previously this only ever showed simulated results and was
// largely redundant with the richer "Backtest & live performance"
// page once that existed.
export function BacktestWidget() {
  const { data, loading } = useBacktest();

  if (loading) return <div className="empty-state">Loading…</div>;

  const live = data?.live_performance;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {live && live.trade_count > 0 ? (
        <div className="metric">
          <span className="metric-label">Live win rate</span>
          <span className="metric-value" style={{ fontSize: 22 }}>
            {live.overall.win_rate_pct != null ? `${live.overall.win_rate_pct}%` : '—'}
          </span>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            {live.trade_count} closed trades ·{' '}
            <span style={{ color: live.overall.total_pnl_abs >= 0 ? 'var(--accent)' : 'var(--danger)' }}>
              {live.overall.total_pnl_abs >= 0 ? '+' : ''}${live.overall.total_pnl_abs}
            </span>
          </span>
          {live.window_note && (
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{live.window_note}</span>
          )}
        </div>
      ) : (
        <div className="empty-state" style={{ padding: 0 }}>No closed trades yet</div>
      )}

      {data?.results && (
        <>
          <div style={{ height: 50 }}>
            <EquityCurveChart
              height={50}
              series={data.results.map((r, i) => ({
                name: r.symbol,
                color: CURVE_COLORS[i % CURVE_COLORS.length],
                points: r.equity_curve || [],
              }))}
            />
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Strategy backtest (simulated)</div>
        </>
      )}
    </div>
  );
}
