import { Link } from 'react-router-dom';

// Dashboard-only widget (no detail page, per the roadmap) — toggle/status
// for the Hermes agent. Stubbed until Phase 3 backend wiring lands
// (hermes_bp blueprint, real tool calls, ANTHROPIC_API_KEY).
export function HermesControlWidget() {
  return (
    <div className="metric">
      <span className="metric-label">Hermes</span>
      <span className="metric-value" style={{ fontSize: 22, color: 'var(--text-muted)' }}>Not wired up yet</span>
      <Link to="/hermes" className="button" style={{ marginTop: 10, textAlign: 'center' }}>
        Open chat
      </Link>
    </div>
  );
}
