import { useState } from 'react';
import { useBacktest } from '../hooks/useBacktest.js';
import { EquityCurveChart } from '../components/EquityCurveChart.jsx';

const CURVE_COLORS = ['#39ff8f', '#7ab8ff', '#ffce54'];
const ASSET_CLASS_LABELS = { stock: 'Stock', forex: 'Forex', crypto: 'Crypto' };

function MetricsTable({ metrics }) {
  return (
    <table className="data-table">
      <thead><tr><th>Regime</th><th>Trades</th><th>Win rate</th><th>Max drawdown</th><th>Sharpe</th><th>Total P&amp;L</th></tr></thead>
      <tbody>
        <tr>
          <td>Overall</td>
          <td>{metrics.overall.trade_count}</td>
          <td>{metrics.overall.win_rate_pct != null ? `${metrics.overall.win_rate_pct}%` : '—'}</td>
          <td>{metrics.overall.max_drawdown_pct != null ? `${metrics.overall.max_drawdown_pct}%` : '—'}</td>
          <td>{metrics.overall.sharpe_ratio ?? '—'}</td>
          <td style={{ color: metrics.overall.total_pnl_abs >= 0 ? 'var(--accent)' : 'var(--danger)', fontWeight: 600 }}>
            ${metrics.overall.total_pnl_abs}
          </td>
        </tr>
        {Object.entries(metrics.by_regime).map(([name, m]) => (
          <tr key={name}>
            <td><span className="regime-badge">{name}</span></td>
            <td>{m.trade_count}</td>
            <td>{m.win_rate_pct != null ? `${m.win_rate_pct}%` : '—'}</td>
            <td>{m.max_drawdown_pct != null ? `${m.max_drawdown_pct}%` : '—'}</td>
            <td>{m.sharpe_ratio ?? '—'}</td>
            <td style={{ color: m.total_pnl_abs >= 0 ? 'var(--accent)' : 'var(--danger)', fontWeight: 600 }}>
              ${m.total_pnl_abs}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function BySymbolTable({ bySymbol }) {
  const entries = Object.entries(bySymbol);
  if (entries.length === 0) return null;
  return (
    <table className="data-table">
      <thead><tr><th>Symbol</th><th>Trades</th><th>Win rate</th><th>Total P&amp;L</th></tr></thead>
      <tbody>
        {entries.map(([sym, m]) => (
          <tr key={sym}>
            <td style={{ fontWeight: 700 }}>{sym}</td>
            <td>{m.trade_count}</td>
            <td>{m.win_rate_pct != null ? `${m.win_rate_pct}%` : '—'}</td>
            <td style={{ color: m.total_pnl_abs >= 0 ? 'var(--accent)' : 'var(--danger)', fontWeight: 600 }}>
              {m.total_pnl_abs >= 0 ? '+' : ''}${m.total_pnl_abs}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AssetClassCard({ assetClass, metrics }) {
  return (
    <div className="section">
      <div className="section-title">{ASSET_CLASS_LABELS[assetClass] || assetClass}</div>
      <div className="stat-grid" style={{ marginBottom: 12 }}>
        <div className="stat-card">
          <span className="metric-label">Trades</span>
          <span className="metric-value">{metrics.overall.trade_count}</span>
        </div>
        <div className="stat-card">
          <span className="metric-label">Win rate</span>
          <span className="metric-value">{metrics.overall.win_rate_pct != null ? `${metrics.overall.win_rate_pct}%` : '—'}</span>
        </div>
        <div className="stat-card">
          <span className="metric-label">Total P&amp;L</span>
          <span className={`metric-value ${metrics.overall.total_pnl_abs >= 0 ? 'positive' : 'negative'}`}>${metrics.overall.total_pnl_abs}</span>
        </div>
        <div className="stat-card">
          <span className="metric-label">Max drawdown</span>
          <span className="metric-value">{metrics.overall.max_drawdown_pct != null ? `${metrics.overall.max_drawdown_pct}%` : '—'}</span>
        </div>
      </div>
      {metrics.by_symbol && Object.keys(metrics.by_symbol).length > 0 && (
        <div className="table-card">
          <BySymbolTable bySymbol={metrics.by_symbol} />
        </div>
      )}
    </div>
  );
}

function LivePerformanceSection({ live }) {
  if (!live) return null;

  const byClass = live.by_asset_class || {};
  const classKeys = Object.keys(byClass);

  return (
    <div className="section">
      <div className="section-title">Live performance (real trades) · updates continuously</div>
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="page-subtitle" style={{ marginBottom: 12 }}>
          Computed from your bot's actual closed trades, refetched every 20s — not the static simulation below.
          {' '}{live.window_note}
        </div>
        <div className="stat-grid">
          <div className="stat-card">
            <span className="metric-label">Closed today</span>
            <span className="metric-value">{live.today?.trade_count ?? 0}</span>
          </div>
          <div className="stat-card">
            <span className="metric-label">Today's P&amp;L</span>
            <span className={`metric-value ${(live.today?.total_pnl_abs ?? 0) >= 0 ? 'positive' : 'negative'}`}>
              {(live.today?.total_pnl_abs ?? 0) >= 0 ? '+' : ''}${live.today?.total_pnl_abs ?? '0.00'}
            </span>
          </div>
          <div className="stat-card">
            <span className="metric-label">Win rate (all-time window)</span>
            <span className="metric-value">{live.overall.win_rate_pct != null ? `${live.overall.win_rate_pct}%` : '—'}</span>
          </div>
          <div className="stat-card">
            <span className="metric-label">Total P&amp;L</span>
            <span className={`metric-value ${live.overall.total_pnl_abs >= 0 ? 'positive' : 'negative'}`}>
              ${live.overall.total_pnl_abs}
            </span>
          </div>
        </div>
      </div>

      {live.trade_count > 0 ? (
        classKeys.length > 0 ? (
          <div className="two-col-grid">
            {classKeys.map((ac) => <AssetClassCard key={ac} assetClass={ac} metrics={byClass[ac]} />)}
          </div>
        ) : (
          <div className="table-card"><MetricsTable metrics={live} /></div>
        )
      ) : (
        <div className="card"><div className="empty-state">No closed trades yet — this fills in as the bot trades live.</div></div>
      )}
    </div>
  );
}

export function BacktestDetail() {
  const { data, loading } = useBacktest();
  const [combined, setCombined] = useState(false);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Backtest &amp; live performance</h1>
          <div className="page-subtitle">Live section above reflects the bot's actual trades, per asset class, refreshed automatically. Static strategy backtest below is a one-time simulation snapshot.</div>
        </div>
        {data?.results?.length > 1 && (
          <button className={`button ${combined ? 'button-accent' : ''}`} onClick={() => setCombined((v) => !v)}>
            {combined ? 'Show separate curves' : 'Show combined overlay'}
          </button>
        )}
      </div>

      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <LivePerformanceSection live={data?.live_performance} />

          <div className="section-title">
            Strategy backtest (static simulation{data?.generated_at ? ` · generated ${data.generated_at}` : ''})
          </div>

          {!data?.results ? (
            <div className="card">
              <div className="empty-state">
                No backtest results yet. Run <code>python -m backtest.runner</code>, commit
                backtest_results.json, and reload.
              </div>
            </div>
          ) : (
            <>
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
                    <MetricsTable metrics={r.metrics} />
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
