import { useState } from 'react';
import { useBacktest } from '../hooks/useBacktest.js';
import { EquityCurveChart } from '../components/EquityCurveChart.jsx';

const CURVE_COLORS = ['#39ff8f', '#7ab8ff', '#ffce54'];

export function BacktestDetail() {
  const { data, loading } = useBacktest();
  const [combined, setCombined] = useState(false);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Backtest results</h1>
          {data?.generated_at && <div className="page-subtitle">Generated {data.generated_at}</div>}
        </div>
        {data?.results?.length > 1 && (
          <button className={`button ${combined ? 'button-accent' : ''}`} onClick={() => setCombined((v) => !v)}>
            {combined ? 'Show separate curves' : 'Show combined overlay'}
          </button>
        )}
      </div>

      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : !data?.results ? (
        <div className="card">
          <div className="empty-state">
            No backtest results yet. Run <code>python -m backtest.runner</code>, commit
            backtest_results.json, and reload.
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {combined && (
            <div className="section">
              <div className="section-title">Combined equity curves</div>
              <div className="card">
                <EquityCurveChart
                  showArea
                  series={data.results.map((r, i) => ({
                    name: r.symbol,
                    color: CURVE_COLORS[i % CURVE_COLORS.length],
                    points: r.equity_curve || [],
                  }))}
                />
                <div className="chart-legend">
                  {data.results.map((r, i) => (
                    <span key={r.symbol}><span className="legend-dot" style={{ background: CURVE_COLORS[i % CURVE_COLORS.length] }} />{r.symbol}</span>
                  ))}
                </div>
              </div>
            </div>
          )}

          {!combined && data.results.map((r, i) => (
            <div className="section" key={r.symbol}>
              <div className="section-title">{r.symbol} · {r.asset_class} · {r.timeframe} · {r.bar_count} bars</div>
              <div className="card" style={{ marginBottom: 12 }}>
                {r.equity_curve && (
                  <EquityCurveChart showArea series={[{ name: r.symbol, color: CURVE_COLORS[i % CURVE_COLORS.length], points: r.equity_curve }]} />
                )}
              </div>
              <div className="table-card">
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
                        <td><span className="regime-badge">{name}</span></td>
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
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
