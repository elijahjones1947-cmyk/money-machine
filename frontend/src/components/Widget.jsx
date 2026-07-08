import { Link } from 'react-router-dom';
import './Widget.css';

// Common shell every dashboard widget wraps: title bar (+ drag handle in
// edit mode), a summary content slot, and tap-through navigation to a
// full detail page when not in edit mode. Detail pages themselves don't
// use this shell's edit-mode/drag behavior at all — only the dashboard
// grid does.
export function Widget({ title, to, editMode, onRemove, children }) {
  const body = (
    <div className="widget">
      <div className="widget-header">
        <span className="widget-title">{title}</span>
        {editMode && <span className="widget-drag-handle">⠿⠿</span>}
      </div>
      <div className="widget-body">{children}</div>
    </div>
  );

  return (
    <div className={`widget-shell${editMode ? ' edit-mode' : ''}`} style={{ position: 'relative', height: '100%' }}>
      {editMode && onRemove && (
        <button
          className="widget-remove-btn"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          title="Remove widget"
        >
          ×
        </button>
      )}
      {editMode || !to ? body : (
        <Link className="widget-link" to={to}>
          {body}
        </Link>
      )}
    </div>
  );
}
