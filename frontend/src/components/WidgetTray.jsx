import { WIDGET_REGISTRY } from '../widgets/registry.js';

// Only rendered in edit mode — lets you add back any widget that isn't
// currently on the grid.
export function WidgetTray({ activeIds, onAdd }) {
  const available = Object.keys(WIDGET_REGISTRY).filter((id) => !activeIds.includes(id));

  return (
    <div className="widget-tray">
      {available.length === 0 ? (
        <span className="widget-tray-empty">All widgets are on your dashboard</span>
      ) : (
        available.map((id) => (
          <button key={id} className="button button-accent" onClick={() => onAdd(id)}>
            + {WIDGET_REGISTRY[id].title}
          </button>
        ))
      )}
    </div>
  );
}
