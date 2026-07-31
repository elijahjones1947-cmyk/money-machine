import { useEffect, useMemo, useState } from 'react';
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

function NoteCard({ note, selectMode, selected, onToggleSelect, onChanged }) {
  const [deleting, setDeleting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(note.content);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const doDelete = async () => {
    setDeleting(true);
    setError(null);
    try {
      await api.deleteNote(note.id);
      onChanged();
    } catch (e) {
      setError(e.message);
      setDeleting(false);
    }
  };

  const startEdit = () => {
    setDraft(note.content);
    setEditing(true);
  };

  const saveEdit = async () => {
    if (!draft.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await api.updateNote(note.id, draft.trim());
      setEditing(false);
      onChanged();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card" style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
        {selectMode && (
          <input
            type="checkbox" checked={selected} onChange={() => onToggleSelect(note.id)}
            style={{ marginTop: 4, flexShrink: 0, width: 16, height: 16 }}
          />
        )}
        {editing ? (
          <textarea
            value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} autoFocus
            style={{
              flex: 1, background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text-primary)',
              padding: '8px 10px', borderRadius: 8, resize: 'vertical', fontFamily: 'inherit', fontSize: 14,
            }}
          />
        ) : (
          <div style={{ whiteSpace: 'pre-wrap', fontSize: 14, flex: 1 }}>{note.content}</div>
        )}
        {!selectMode && (
          editing ? (
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
              <button className="button button-accent" onClick={saveEdit} disabled={saving || !draft.trim()}>
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button className="button" onClick={() => setEditing(false)} disabled={saving}>Cancel</button>
            </div>
          ) : confirming ? (
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
              <button className="button button-danger" onClick={doDelete} disabled={deleting}>
                {deleting ? 'Deleting…' : 'Confirm'}
              </button>
              <button className="button" onClick={() => setConfirming(false)} disabled={deleting}>Cancel</button>
            </div>
          ) : (
            <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
              <button className="button" onClick={startEdit}>Edit</button>
              <button className="button" onClick={() => setConfirming(true)}>Delete</button>
            </div>
          )
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

// Concatenates notes' text (in the order given, i.e. the same
// newest-first order they're displayed in) into one block, separated
// by a light divider so it's still clear where each note started.
function compileText(notes) {
  return notes.map((n) => n.content).join('\n\n---\n\n');
}

export function NotesDetail() {
  const [notes, setNotes] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [bulkConfirming, setBulkConfirming] = useState(null); // 'selected' | 'all' | null
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState(null);

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

  const toggleSelectMode = () => {
    setSelectMode((v) => !v);
    setSelectedIds(new Set());
  };

  const toggleSelect = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectedNotes = useMemo(
    () => (notes || []).filter((n) => selectedIds.has(n.id)),
    [notes, selectedIds],
  );

  const runBulkDelete = async (targetNotes) => {
    setBulkBusy(true);
    setBulkError(null);
    try {
      await Promise.all(targetNotes.map((n) => api.deleteNote(n.id)));
      setSelectedIds(new Set());
      setBulkConfirming(null);
      await refetch();
    } catch (e) {
      setBulkError(e.message);
    } finally {
      setBulkBusy(false);
    }
  };

  const runCompile = async (targetNotes) => {
    if (targetNotes.length === 0) return;
    setBulkBusy(true);
    setBulkError(null);
    try {
      await api.createNote(compileText(targetNotes));
      setSelectedIds(new Set());
      await refetch();
    } catch (e) {
      setBulkError(e.message);
    } finally {
      setBulkBusy(false);
    }
  };

  const hasNotes = notes && notes.length > 0;

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

      {hasNotes && (
        <div className="card" style={{ marginBottom: 12, display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          <button className={`button ${selectMode ? 'button-accent' : ''}`} onClick={toggleSelectMode}>
            {selectMode ? `Selecting (${selectedIds.size})` : 'Select'}
          </button>

          {selectMode && selectedIds.size > 0 && (
            bulkConfirming === 'selected' ? (
              <>
                <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Delete {selectedIds.size} note(s)?</span>
                <button className="button button-danger" onClick={() => runBulkDelete(selectedNotes)} disabled={bulkBusy}>
                  {bulkBusy ? 'Deleting…' : 'Confirm'}
                </button>
                <button className="button" onClick={() => setBulkConfirming(null)} disabled={bulkBusy}>Cancel</button>
              </>
            ) : (
              <>
                <button className="button button-danger" onClick={() => setBulkConfirming('selected')} disabled={bulkBusy}>
                  Delete selected
                </button>
                <button className="button" onClick={() => runCompile(selectedNotes)} disabled={bulkBusy}>
                  {bulkBusy ? 'Compiling…' : 'Compile selected'}
                </button>
              </>
            )
          )}

          <div style={{ flex: 1 }} />

          {bulkConfirming === 'all' ? (
            <>
              <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Delete all {notes.length} notes?</span>
              <button className="button button-danger" onClick={() => runBulkDelete(notes)} disabled={bulkBusy}>
                {bulkBusy ? 'Deleting…' : 'Confirm'}
              </button>
              <button className="button" onClick={() => setBulkConfirming(null)} disabled={bulkBusy}>Cancel</button>
            </>
          ) : (
            <>
              <button className="button" onClick={() => runCompile(notes)} disabled={bulkBusy}>Compile all</button>
              <button className="button button-danger" onClick={() => setBulkConfirming('all')} disabled={bulkBusy}>Delete all</button>
            </>
          )}
        </div>
      )}
      {bulkError && <div className="error-text" style={{ marginBottom: 12 }}>{bulkError}</div>}

      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : !hasNotes ? (
        <div className="card"><div className="empty-state">No notes yet</div></div>
      ) : (
        notes.map((note) => (
          <NoteCard
            key={note.id} note={note} selectMode={selectMode}
            selected={selectedIds.has(note.id)} onToggleSelect={toggleSelect}
            onChanged={refetch}
          />
        ))
      )}
    </div>
  );
}
