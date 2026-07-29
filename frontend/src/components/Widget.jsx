import { Link } from 'react-router-dom';
import './Widget.css';

// Common shell every dashboard widget wraps: title bar, a summary
// content slot, and tap-through navigation to a full detail page.
// `description` is optional -- no widget used one before AlertHealthWidget,
// so this establishes the pattern (a small (i) glyph next to the title
// with a native `title` attribute for the tooltip -- zero new
// dependencies, works with keyboard/AT via the browser's own tooltip
// handling) rather than inventing a bespoke per-widget approach. Widgets
// that don't pass it render exactly as before.
export function Widget({ title, description, to, children }) {
  const body = (
    <div className="widget">
      <div className="widget-header">
        <span className="widget-title">{title}</span>
        {description && (
          <span className="widget-info" title={description} aria-label={description}>
            ⓘ
          </span>
        )}
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
