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

  const latest = notes[0];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div className="metric">
        <span className="metric-label">Notes</span>
        <span className="metric-value" style={{ fontSize: 28 }}>{notes.length}</span>
      </div>
      <div style={{
        fontSize: 12, color: 'var(--text-secondary)', overflow: 'hidden',
        textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>
        Latest: {latest.content}
      </div>
    </div>
  );
}
