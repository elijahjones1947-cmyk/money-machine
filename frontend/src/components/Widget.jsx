import { Link } from 'react-router-dom';
import './Widget.css';

// Common shell every dashboard widget wraps: title bar, a summary
// content slot, and tap-through navigation to a full detail page.
export function Widget({ title, to, children }) {
  const body = (
    <div className="widget">
      <div className="widget-header">
        <span className="widget-title">{title}</span>
      </div>
      <div className="widget-body">{children}</div>
    </div>
  );

  return (
    <div className="widget-shell">
      {to ? <Link className="widget-link" to={to}>{body}</Link> : body}
    </div>
  );
}
