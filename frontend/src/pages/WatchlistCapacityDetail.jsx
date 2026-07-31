import { useDashboard } from '../hooks/useDashboard.js';

const ASSET_CLASSES = ['stock', 'forex', 'crypto'];

// Read-only expansion of the WatchlistCapacityWidget card: same
// size/cap numbers (watched_symbols vs. risk_caps.max_open_positions,
// live from /api/dashboard, never hardcoded) plus the actual watched
// symbol list and a capacity bar per class. Deliberately does NOT
// duplicate Settings' editable risk-lever fields -- this page is
// "what's the capacity picture right now," Settings stays "go change
// it."
export function WatchlistCapacityDetail() {
  const { data, loading } = useDashboard();
  const watched = data?.watched_symbols || {};
  const caps = data?.risk_caps || {};

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Bucket of Funds capacity</h1>
          <div className="page-subtitle">
            Read-only view of watchlist size vs. the max_open_positions cap, per asset class. To change the
            watchlist or the cap itself, use Settings.
          </div>
        </div>
      </div>

      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : (
        ASSET_CLASSES.map((ac) => {
          const symbols = watched[ac] || [];
          const cap = caps[ac]?.max_open_positions;
          const pct = cap ? Math.min(100, (symbols.length / cap) * 100) : 0;
          const atCap = cap != null && symbols.length >= cap;
          return (
            <div className="section" key={ac}>
              <div className="section-title" style={{ textTransform: 'capitalize' }}>{ac}</div>
              <div className="card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                  <span
                    className="pill"
                    style={atCap ? { color: 'var(--danger)', borderColor: 'var(--danger)' } : undefined}
                    title={atCap ? 'Watchlist size is at (or over) max_open_positions' : undefined}
                  >
                    {symbols.length}/{cap ?? '?'} watched
                  </span>
                </div>
                <div style={{
                  position: 'relative', height: 10, borderRadius: 5,
                  background: 'var(--bg-elevated)', border: '1px solid var(--border)', overflow: 'hidden', marginBottom: 12,
                }}>
                  <div style={{
                    position: 'absolute', top: 0, bottom: 0, left: 0, width: `${pct}%`,
                    background: atCap ? 'var(--danger)' : 'var(--accent)', borderRadius: 5, transition: 'width 0.3s ease',
                  }} />
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {symbols.length === 0
                    ? <span className="empty-state" style={{ padding: 0 }}>none watched</span>
                    : symbols.map((s) => <span key={s} className="pill">{s}</span>)}
                </div>
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}
