import { useDashboard } from '../hooks/useDashboard.js';

const ASSET_CLASSES = ['stock', 'forex', 'crypto'];

// Both numbers pulled live from /api/dashboard -- watched_symbols
// (state.watched_symbols) and risk_caps (state.risk_caps, the live/
// editable copy of config.RISK_CONFIG, same source Settings' "max open
// positions" field reads/writes) -- never hardcoded, so this stays
// correct through a watchlist change or a Settings-driven cap edit with
// no code change needed on either side.
export function WatchlistCapacityWidget() {
  const { data, loading } = useDashboard();

  if (loading) return <div className="empty-state">Loading…</div>;
  if (!data) return <div className="empty-state">No data</div>;

  const watched = data.watched_symbols || {};
  const caps = data.risk_caps || {};

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {ASSET_CLASSES.map((ac) => {
        const size = (watched[ac] || []).length;
        const cap = caps[ac]?.max_open_positions;
        const atCap = cap != null && size >= cap;
        return (
          <div key={ac} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ textTransform: 'capitalize', fontSize: 13 }}>{ac}</span>
            <span
              className="pill"
              style={atCap ? { color: 'var(--danger)', borderColor: 'var(--danger)' } : undefined}
              title={atCap ? 'Watchlist size is at (or over) max_open_positions' : undefined}
            >
              {size}/{cap ?? '?'}
            </span>
          </div>
        );
      })}
    </div>
  );
}
