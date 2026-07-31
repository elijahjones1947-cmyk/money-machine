import { useEffect, useState } from 'react';
import { api } from '../api.js';

const LEVEL_COLOR = {
  ERROR: 'var(--danger)',
  WARNING: 'var(--text-secondary)',
};

// Full error feed -- the widget on the dashboard is hardcoded to the
// last 5 with each message ellipsis-truncated; this page is the
// dedicated place to read the whole thing. Independent fetch, same
// pattern as NotesWidget/RecentErrorsWidget -- /api/errors isn't part
// of /api/dashboard's payload.
export function RecentErrorsDetail() {
  const [errors, setErrors] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.listErrors(200).then((r) => setErrors(r.errors)).catch((e) => setError(e.message));
  }, []);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Recent errors</h1>
          <div className="page-subtitle">Full error log (up to the last 200), dust-position noise already filtered out server-side.</div>
        </div>
      </div>

      {error && <div className="error-text" style={{ marginBottom: 12 }}>{error}</div>}

      {errors === null ? (
        <div className="empty-state">Loading…</div>
      ) : errors.length === 0 ? (
        <div className="card"><div className="empty-state">No recent errors</div></div>
      ) : (
        errors.map((e, i) => (
          <div className="card" style={{ marginBottom: 10 }} key={i}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 6, flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ color: LEVEL_COLOR[e.level] || 'var(--text-secondary)', fontWeight: 700, fontSize: 12 }}>
                  {e.level}
                </span>
                {e.source && (
                  <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{e.source}</span>
                )}
              </div>
              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {e.occurred_at ? new Date(e.occurred_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}
              </span>
            </div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
              {e.message}
            </div>
          </div>
        ))
      )}
    </div>
  );
}
