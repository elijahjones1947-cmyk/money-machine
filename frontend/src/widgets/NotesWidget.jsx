import { useEffect, useState } from 'react';
import { api } from '../api.js';

// Independent fetch, not part of useDashboard's payload -- notes are
// pure dashboard scratch space with no relationship to trading state,
// so there's no reason to bloat /api/dashboard's response for it.
export function NotesWidget() {
  const [notes, setNotes] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.listNotes().then((r) => setNotes(r.notes)).catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="empty-state">{error}</div>;
  if (notes === null) return <div className="empty-state">Loading…</div>;
  if (notes.length === 0) return <div className="empty-state">No notes yet</div>;

  // Show several recent notes, each clamped to 2 lines but scrollable
  // within the widget body (Widget.css already sets overflow:auto
  // there) -- a single ellipsis-truncated line hid everything past the
  // very latest note; this at least surfaces the last 5 at a glance.
  const recent = notes.slice(0, 5);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, height: '100%' }}>
      <div className="metric">
        <span className="metric-label">Notes</span>
        <span className="metric-value" style={{ fontSize: 28 }}>{notes.length}</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {recent.map((note) => (
          <div key={note.id} style={{
            fontSize: 12, color: 'var(--text-secondary)', overflow: 'hidden',
            textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
            borderBottom: '1px solid var(--border)', paddingBottom: 6,
          }}>
            {note.content}
          </div>
        ))}
      </div>
    </div>
  );
}
