import { useDashboard } from '../hooks/useDashboard.js';

const REGIME_COLOR = {
  trending: 'var(--regime-trending)',
  choppy: 'var(--regime-choppy)',
  volatile: 'var(--regime-volatile)',
  unknown: 'var(--regime-unknown)',
};

export function RegimeWidget() {
  const { data, loading } = useDashboard();
  const regimes = data?.regimes ?? [];

  if (loading) return <div className="empty-state">Loading…</div>;
  if (regimes.length === 0) return <div className="empty-state">No regime data yet</div>;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {regimes.map((r) => (
        <div key={r.symbol} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 13 }}>
          <span>{r.symbol}</span>
          <span className="pill">
            <span className="pill-dot" style={{ background: REGIME_COLOR[r.regime] || 'var(--text-muted)' }} />
            {r.regime}
          </span>
        </div>
      ))}
    </div>
  );
}
