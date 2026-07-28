import { useEffect, useState } from 'react';
import { api } from '../api.js';

function CreateNoteForm({ onCreated }) {
  const [content, setContent] = useState('');
  const [status, setStatus] = useState(null);

  const create = async (e) => {
    e.preventDefault();
    if (!content.trim()) return;
    setStatus('saving');
    try {
      await api.createNote(content.trim());
      setContent('');
      setStatus(null);
      onCreated();
    } catch (err) {
      setStatus(err.message);
    }
  };

  return (
    <form onSubmit={create} className="card" style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <textarea
          value={content} onChange={(e) => setContent(e.target.value)}
          placeholder="Write a note…" rows={3}
          style={{
            background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text-primary)',
            padding: '8px 10px', borderRadius: 8, resize: 'vertical', fontFamily: 'inherit', fontSize: 14,
          }}
        />
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button className="button button-accent" type="submit" disabled={status === 'saving' || !content.trim()}>
            {status === 'saving' ? 'Saving…' : 'Add note'}
          </button>
          {status && status !== 'saving' && <span className="error-text">{status}</span>}
        </div>
      </div>
    </form>
  );
}

function NoteCard({ note, onDeleted }) {
  const [deleting, setDeleting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState(null);

  const doDelete = async () => {
    setDeleting(true);
    setError(null);
    try {
      await api.deleteNote(note.id);
      onDeleted();
    } catch (e) {
      setError(e.message);
      setDeleting(false);
    }
  };

  return (
    <div className="card" style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
        <div style={{ whiteSpace: 'pre-wrap', fontSize: 14, flex: 1 }}>{note.content}</div>
        {confirming ? (
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
            <button className="button button-danger" onClick={doDelete} disabled={deleting}>
              {deleting ? 'Deleting…' : 'Confirm'}
            </button>
            <button className="button" onClick={() => setConfirming(false)} disabled={deleting}>Cancel</button>
          </div>
        ) : (
          <button className="button" style={{ flexShrink: 0 }} onClick={() => setConfirming(true)}>Delete</button>
        )}
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 8 }}>
        {note.created_at ? new Date(note.created_at).toLocaleString(undefined, {
          month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
        }) : '—'}
      </div>
      {error && <div className="error-text" style={{ marginTop: 4 }}>{error}</div>}
    </div>
  );
}

export function NotesDetail() {
  const [notes, setNotes] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const refetch = async () => {
    try {
      const result = await api.listNotes();
      setNotes(result.notes);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Notes</h1>
          <div className="page-subtitle">
            Free-text scratch space, persists indefinitely -- a note only ever goes away if you delete it.
          </div>
        </div>
      </div>

      {error && <div className="error-text" style={{ marginBottom: 12 }}>{error}</div>}

      <CreateNoteForm onCreated={refetch} />

      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : !notes || notes.length === 0 ? (
        <div className="card"><div className="empty-state">No notes yet</div></div>
      ) : (
        notes.map((note) => <NoteCard key={note.id} note={note} onDeleted={refetch} />)
      )}
    </div>
  );
}
