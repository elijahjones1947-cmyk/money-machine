import { useBacktest } from '../hooks/useBacktest.js';

export function BacktestDetail() {
  const { data, loading } = useBacktest();

  return (
    <div>
      <h2>Backtest results</h2>
      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : !data?.results ? (
        <div className="empty-state">
          No backtest results yet. Run <code>python -m backtest.runner</code>, commit
          backtest_results.json, and reload.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
          <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>Generated {data.generated_at}</div>
          {data.results.map((r) => (
            <div key={r.symbol}>
              <h3 style={{ marginBottom: 8 }}>{r.symbol} <span style={{ color: 'var(--text-secondary)', fontWeight: 400 }}>({r.asset_class} · {r.timeframe} · {r.bar_count} bars)</span></h3>
              <table className="data-table">
                <thead><tr><th>Regime</th><th>Trades</th><th>Win rate</th><th>Max drawdown</th><th>Sharpe</th><th>Total P&amp;L</th></tr></thead>
                <tbody>
                  <tr>
                    <td>Overall</td>
                    <td>{r.metrics.overall.trade_count}</td>
                    <td>{r.metrics.overall.win_rate_pct}%</td>
                    <td>{r.metrics.overall.max_drawdown_pct}%</td>
                    <td>{r.metrics.overall.sharpe_ratio}</td>
                    <td>${r.metrics.overall.total_pnl_abs}</td>
                  </tr>
                  {Object.entries(r.metrics.by_regime).map(([name, m]) => (
                    <tr key={name}>
                      <td style={{ textTransform: 'capitalize' }}>{name}</td>
                      <td>{m.trade_count}</td>
                      <td>{m.win_rate_pct}%</td>
                      <td>{m.max_drawdown_pct}%</td>
                      <td>{m.sharpe_ratio}</td>
                      <td>${m.total_pnl_abs}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
