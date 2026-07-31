import { useState } from 'react';
import { useBacktest } from '../hooks/useBacktest.js';
import { EquityCurveChart } from '../components/EquityCurveChart.jsx';

const CURVE_COLORS = ['#39ff8f', '#7ab8ff', '#ffce54'];
const ASSET_CLASS_LABELS = { stock: 'Stock', forex: 'Forex', crypto: 'Crypto' };

const EXIT_REASON_LABELS = {
  take_profit: 'Take profit', stop_loss: 'Stop loss', trailing_stop: 'Trailing stop',
  momentum_exit: 'Momentum exit', end_of_data: 'End of data',
};

// backtest_results.json carries the full simulated trade-by-trade list
// per symbol (backtest/runner.py's run_one() -> "trades"), not just the
// aggregate metrics -- previously computed and written but never
// rendered anywhere on this page. Collapsed by default since a 6-month
// backtest can easily produce 50+ rows per symbol.
function BacktestTradesTable({ trades }) {
  const [expanded, setExpanded] = useState(false);
  if (!trades || trades.length === 0) return null;

  return (
    <div style={{ marginTop: 12 }}>
      <button className="button" onClick={() => setExpanded((v) => !v)}>
        {expanded ? 'Hide' : 'Show'} {trades.length} simulated trades
      </button>
      {expanded && (
        <div className="table-card" style={{ marginTop: 10 }}>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr><th>Entry</th><th>Exit</th><th>Exit reason</th><th>Regime</th><th>Hold (bars)</th><th>P&amp;L %</th><th>P&amp;L $</th></tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr key={i}>
                    <td style={{ whiteSpace: 'nowrap' }}>{t.entry_time ? new Date(t.entry_time).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>{t.exit_time ? new Date(t.exit_time).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}</td>
                    <td>{EXIT_REASON_LABELS[t.exit_reason] || t.exit_reason || '—'}</td>
                    <td><span className="regime-badge">{t.regime || 'unknown'}</span></td>
                    <td>{t.hold_bars ?? '—'}</td>
                    <td style={{ color: t.pnl_pct >= 0 ? 'var(--accent)' : 'var(--danger)' }}>
                      {t.pnl_pct != null ? `${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(2)}%` : '—'}
                    </td>
                    <td style={{ color: t.pnl_abs >= 0 ? 'var(--accent)' : 'var(--danger)', fontWeight: 600 }}>
                      {t.pnl_abs != null ? `${t.pnl_abs >= 0 ? '+' : ''}$${t.pnl_abs.toFixed(2)}` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function MetricsTable({ metrics }) {
  return (
    <table className="data-table">
      <thead><tr><th>Regime</th><th>Trades</th><th>Win rate</th><th>Avg P&amp;L %</th><th>Max drawdown</th><th>Sharpe</th><th>Total P&amp;L</th></tr></thead>
      <tbody>
        <tr>
          <td>Overall</td>
          <td>{metrics.overall.trade_count}</td>
          <td>{metrics.overall.win_rate_pct != null ? `${metrics.overall.win_rate_pct}%` : '—'}</td>
          <td>{metrics.overall.avg_pnl_pct != null ? `${metrics.overall.avg_pnl_pct}%` : '—'}</td>
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
            <td>{m.avg_pnl_pct != null ? `${m.avg_pnl_pct}%` : '—'}</td>
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
        <div className="stat-card">
          <span className="metric-label">Avg P&amp;L %</span>
          <span className="metric-value">{metrics.overall.avg_pnl_pct != null ? `${metrics.overall.avg_pnl_pct}%` : '—'}</span>
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
          {live.initial_capital != null && ` Max drawdown % is measured against a backed-into baseline of $${live.initial_capital.toLocaleString()} (current equity minus this window's realized P&L), not a fixed nominal starting value.`}
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
            <span className="metric-label">Avg P&amp;L %</span>
            <span className="metric-value">{live.overall.avg_pnl_pct != null ? `${live.overall.avg_pnl_pct}%` : '—'}</span>
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
                  <BacktestTradesTable trades={r.trades} />
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
