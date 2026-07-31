import { Widget } from './Widget.jsx';
import { WIDGET_REGISTRY, DEFAULT_WIDGET_IDS } from '../widgets/registry.js';
import './DashboardGrid.css';

// Fixed, non-editable dashboard layout -- the old drag/resize "Edit
// layout" mode (react-grid-layout + server-persisted positions) has
// been removed entirely per product decision; every widget always
// renders in the same order at a responsive fixed size.
export function DashboardGrid() {
  return (
    <div>
      <div className="dashboard-toolbar">
        <span className="dashboard-toolbar-title">Dashboard</span>
      </div>

      <div className="dashboard-grid">
        {DEFAULT_WIDGET_IDS.map((id) => {
          const { title, description, Component, to, compact } = WIDGET_REGISTRY[id];
          return (
            <Widget key={id} title={title} description={description} to={to} compact={compact}>
              <Component />
            </Widget>
          );
        })}
      </div>
    </div>
  );
}
