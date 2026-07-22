import { useDashboard } from '../hooks/useDashboard.js';

// Mirrors config.REGIME_CONFIG's thresholds server-side (regime.py /
// config.py) purely for the gauge tick mark -- classification itself
// always comes from the backend's `regime` field, this is just a
// visual reference so the gauge and the label never disagree.
const ADX_TREND_THRESHOLD = 25;
const BB_WIDTH_VOLATILE_THRESHOLD = { stock: 5.0, forex: 1.5, crypto: 10.0 };

const REGIME_META = {
  trending: { color: 'var(--regime-trending)', dim: 'var(--regime-trending-dim)', label: 'Trending', hint: 'Strong directional move (ADX above threshold)' },
  choppy: { color: 'var(--regime-choppy)', dim: 'var(--regime-choppy-dim)', label: 'Choppy', hint: 'No clear trend, low volatility' },
  volatile: { color: 'var(--regime-volatile)', dim: 'var(--regime-volatile-dim)', label: 'Volatile', hint: 'Wide swings (Bollinger Band width above threshold)' },
  unknown: { color: 'var(--regime-unknown)', dim: 'var(--regime-unknown-dim)', label: 'Unknown', hint: 'Not enough bars classified yet' },
};

function timeAgo(iso) {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (isNaN(then)) return null;
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function Gauge({ value, max, threshold, color }) {
  const pct = value == null ? 0 : Math.min(100, (value / max) * 100);
  const thresholdPct = Math.min(100, (threshold / max) * 100);
  return (
    <div className="regime-gauge-track">
      <div className="regime-gauge-fill" style={{ width: `${pct}%`, background: color }} />
      <div className="regime-gauge-threshold" style={{ left: `${thresholdPct}%` }} />
    </div>
  );
}

function RegimeCard({ r }) {
  const meta = REGIME_META[r.regime] || REGIME_META.unknown;
  const bbThreshold = BB_WIDTH_VOLATILE_THRESHOLD[r.asset_class] ?? 5.0;
  const ago = timeAgo(r.recorded_at);

  return (
    <div className="regime-card" style={{ borderColor: meta.color, background: meta.dim }}>
      <div className="regime-card-top">
        <div>
          <div className="regime-card-symbol">{r.symbol}</div>
          <div className="regime-card-class">{r.asset_class}</div>
        </div>
        <span className="regime-card-badge" style={{ background: meta.color }}>
          {meta.label}
        </span>
      </div>

      <div className="regime-card-gauges">
        <div className="regime-gauge-row">
          <span className="regime-gauge-label">ADX {r.adx ?? '—'}</span>
          <Gauge value={r.adx} max={50} threshold={ADX_TREND_THRESHOLD} color={meta.color} />
        </div>
        <div className="regime-gauge-row">
          <span className="regime-gauge-label">BB width {r.bb_width_pct ?? '—'}%</span>
          <Gauge value={r.bb_width_pct} max={bbThreshold * 2} threshold={bbThreshold} color={meta.color} />
        </div>
      </div>

      <div className="regime-card-footer">
        <span title={meta.hint}>{meta.hint}</span>
        {ago && <span>{ago}</span>}
      </div>
    </div>
  );
}

export function RegimeDetail() {
  const { data, loading } = useDashboard();
  const regimes = data?.regimes ?? [];

  const byClass = { stock: [], forex: [], crypto: [] };
  for (const r of regimes) {
    (byClass[r.asset_class] ??= []).push(r);
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Market regime</h1>
          <div className="page-subtitle">Live classification per watched symbol, refreshed every 15s. Green = trending, yellow = choppy, red = volatile.</div>
        </div>
      </div>
      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : regimes.length === 0 ? (
        <div className="card"><div className="empty-state">No regime data yet</div></div>
      ) : (
        Object.entries(byClass).filter(([, rows]) => rows.length > 0).map(([ac, rows]) => (
          <div className="section" key={ac}>
            <div className="section-title">{ac}</div>
            <div className="regime-grid">
              {rows.map((r) => <RegimeCard key={r.symbol} r={r} />)}
            </div>
          </div>
        ))
      )}
    </div>
  );
}
