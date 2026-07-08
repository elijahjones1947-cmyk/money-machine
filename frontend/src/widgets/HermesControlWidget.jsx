import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api.js';

// Dashboard-only widget (no detail page, per the roadmap) — status +
// link into the full Hermes chat page.
export function HermesControlWidget() {
  const [configured, setConfigured] = useState(null);

  useEffect(() => {
    api.hermesHistory().then((r) => setConfigured(r.configured)).catch(() => setConfigured(false));
  }, []);

  return (
    <div className="metric">
      <span className="metric-label">Hermes</span>
      <span className="metric-value" style={{ fontSize: 22, color: configured ? 'var(--accent)' : 'var(--text-muted)' }}>
        {configured === null ? 'Checking…' : configured ? 'Ready' : 'Not configured'}
      </span>
      <Link to="/hermes" className="button" style={{ marginTop: 10, textAlign: 'center' }}>
        Open chat
      </Link>
    </div>
  );
}
