import { useState } from 'react';
import { useBacktest } from '../hooks/useBacktest.js';
import { EquityCurveChart } from '../components/EquityCurveChart.jsx';

const CURVE_COLORS = ['#39ff8f', '#7ab8ff', '#ffce54'];

export function BacktestDetail() {
  const { data, loading } = useBacktest();
  const [combined, setCombined] = useState(false);

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2>Backtest results</h2>
        {data?.results?.length > 1 && (
          <button className={`button ${combined ? 'button-accent' : ''}`} onClick={() => setCombined((v) => !v)}>
            {combined ? 'Show separate curves' : 'Show combined overlay'}
          </button>
        )}
      </div>

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

          {combined && (
            <div>
              <h3 style={{ marginBottom: 8 }}>Combined equity curves</h3>
              <EquityCurveChart
                series={data.results.map((r, i) => ({
                  name: r.symbol,
                  color: CURVE_COLORS[i % CURVE_COLORS.length],
                  points: r.equity_curve || [],
                }))}
              />
              <div style={{ display: 'flex', gap: 16, marginTop: 8, fontSize: 12 }}>
                {data.results.map((r, i) => (
                  <span key={r.symbol} style={{ color: CURVE_COLORS[i % CURVE_COLORS.length] }}>● {r.symbol}</span>
                ))}
              </div>
            </div>
          )}

          {!combined && data.results.map((r, i) => (
            <div key={r.symbol}>
              <h3 style={{ marginBottom: 8 }}>{r.symbol} <span style={{ color: 'var(--text-secondary)', fontWeight: 400 }}>({r.asset_class} · {r.timeframe} · {r.bar_count} bars)</span></h3>
              {r.equity_curve && (
                <EquityCurveChart series={[{ name: r.symbol, color: CURVE_COLORS[i % CURVE_COLORS.length], points: r.equity_curve }]} />
              )}
              <table className="data-table" style={{ marginTop: 12 }}>
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
