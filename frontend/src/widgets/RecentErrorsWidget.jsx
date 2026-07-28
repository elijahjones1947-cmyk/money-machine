import { useEffect, useState } from 'react';
import { api } from '../api.js';

// Independent fetch (like NotesWidget) -- /api/errors is its own route,
// not part of /api/dashboard's payload. Dust-position noise is already
// filtered out server-side (server.py's _is_dust_noise), so what lands
// here is real signal by construction, not something this widget has to
// re-filter.
export function RecentErrorsWidget() {
  const [errors, setErrors] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.listErrors(5).then((r) => setErrors(r.errors)).catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="empty-state">{error}</div>;
  if (errors === null) return <div className="empty-state">Loading…</div>;
  if (errors.length === 0) return <div className="empty-state">No recent errors</div>;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {errors.map((e, i) => (
        <div key={i} style={{ fontSize: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
            <span style={{ color: e.level === 'ERROR' ? 'var(--danger)' : 'var(--text-secondary)', fontWeight: 600 }}>
              {e.level}
            </span>
            <span style={{ color: 'var(--text-secondary)' }}>
              {e.occurred_at ? new Date(e.occurred_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}
            </span>
          </div>
          <div style={{
            color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis',
            whiteSpace: 'nowrap', marginTop: 2,
          }} title={e.message}>
            {e.message}
          </div>
        </div>
      ))}
    </div>
  );
}
