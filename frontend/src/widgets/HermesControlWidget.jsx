import { useEffect, useState } from 'react';
import { api } from '../api.js';

// The whole card is now a Link into /hermes (registry.js) like every
// other widget with a detail page, so this no longer needs its own
// inner "Open chat" link -- that used to be the ONLY way in (registry
// had to: null), and nesting a second <a> inside the card-wide Link
// Widget.jsx now renders would be invalid HTML/broken click behavior.
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
    </div>
  );
}
