import { useEffect, useState, useCallback } from 'react';
import { Responsive, WidthProvider } from 'react-grid-layout/legacy';
import 'react-grid-layout/css/styles.css';
import { Widget } from './Widget.jsx';
import { WidgetTray } from './WidgetTray.jsx';
import { WIDGET_REGISTRY, defaultLayout } from '../widgets/registry.js';
import { api } from '../api.js';
import './DashboardGrid.css';

const ResponsiveGridLayout = WidthProvider(Responsive);

export function DashboardGrid() {
  const [editMode, setEditMode] = useState(false);
  const [layout, setLayout] = useState(null); // array of {i,x,y,w,h}
  const [ready, setReady] = useState(false);

  useEffect(() => {
    api
      .getLayout()
      .then((r) => {
        if (r.layout && Array.isArray(r.layout) && r.layout.length > 0) {
          setLayout(r.layout);
        } else {
          setLayout(defaultLayout());
        }
      })
      .catch(() => setLayout(defaultLayout()))
      .finally(() => setReady(true));
  }, []);

  const persist = useCallback((nextLayout) => {
    api.saveLayout(nextLayout).catch(() => {
      // Best-effort — a failed layout save shouldn't interrupt the UI,
      // it'll just re-fetch the last saved arrangement next visit.
    });
  }, []);

  const handleLayoutChange = (newLayout) => {
    setLayout(newLayout);
  };

  const handleDragOrResizeStop = (newLayout) => {
    setLayout(newLayout);
    persist(newLayout);
  };

  const addWidget = (id) => {
    const { w, h } = WIDGET_REGISTRY[id].defaultSize;
    const maxY = layout.reduce((m, item) => Math.max(m, item.y + item.h), 0);
    const nextLayout = [...layout, { i: id, x: 0, y: maxY, w, h }];
    setLayout(nextLayout);
    persist(nextLayout);
  };

  const removeWidget = (id) => {
    const nextLayout = layout.filter((item) => item.i !== id);
    setLayout(nextLayout);
    persist(nextLayout);
  };

  if (!ready || !layout) return <div className="empty-state">Loading dashboard…</div>;

  const activeIds = layout.map((item) => item.i).filter((id) => WIDGET_REGISTRY[id]);

  return (
    <div>
      <div className="dashboard-toolbar">
        <span className="dashboard-toolbar-title">Dashboard</span>
        <div className="edit-toggle">
          <button className={`button ${editMode ? 'button-accent' : ''}`} onClick={() => setEditMode((v) => !v)}>
            {editMode ? 'Done editing' : 'Edit layout'}
          </button>
        </div>
      </div>

      <ResponsiveGridLayout
        className="layout"
        layouts={{ lg: layout }}
        breakpoints={{ lg: 1200, md: 900, sm: 640, xxs: 0 }}
        cols={{ lg: 12, md: 12, sm: 6, xxs: 1 }}
        margin={{ lg: [16, 16], md: [16, 16], sm: [12, 12], xxs: [10, 10] }}
        rowHeight={70}
        isDraggable={editMode}
        isResizable={editMode}
        draggableHandle=".widget-drag-handle"
        onLayoutChange={handleLayoutChange}
        onDragStop={handleDragOrResizeStop}
        onResizeStop={handleDragOrResizeStop}
      >
        {activeIds.map((id) => {
          const { title, Component, to } = WIDGET_REGISTRY[id];
          return (
            <div key={id}>
              <Widget title={title} to={to} editMode={editMode} onRemove={() => removeWidget(id)}>
                <Component />
              </Widget>
            </div>
          );
        })}
      </ResponsiveGridLayout>

      {editMode && <WidgetTray activeIds={activeIds} onAdd={addWidget} />}
    </div>
  );
}
