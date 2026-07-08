import { useBacktest } from '../hooks/useBacktest.js';

export function BacktestWidget() {
  const { data, loading } = useBacktest();

  if (loading) return <div className="empty-state">Loading…</div>;
  if (!data?.results) {
    return <div className="empty-state">No backtest results yet — run the backtester and refresh.</div>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {data.results.map((r) => (
        <div key={r.symbol} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
          <span>{r.symbol}</span>
          <span style={{ color: r.metrics.overall.win_rate_pct >= 45 ? 'var(--accent)' : 'var(--text-secondary)' }}>
            {r.metrics.overall.win_rate_pct}% win · {r.metrics.overall.trade_count} trades
          </span>
        </div>
      ))}
    </div>
  );
}
