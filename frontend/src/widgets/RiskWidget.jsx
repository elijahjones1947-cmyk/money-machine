import { useDashboard } from '../hooks/useDashboard.js';

export function RiskWidget() {
  const { data, loading } = useDashboard();
  const risk = data?.risk_state;

  if (loading) return <div className="empty-state">Loading…</div>;
  if (!risk) return <div className="empty-state">No risk data</div>;

  const anyHalted = risk.account_halted || risk.stock_halted || risk.forex_halted || risk.crypto_halted;

  return (
    <div className="metric">
      <span className="metric-label">Risk state</span>
      <span className={`metric-value ${anyHalted ? 'negative' : 'positive'}`} style={{ fontSize: 28 }}>
        {risk.account_halted ? 'Account halted' : anyHalted ? 'Partial halt' : 'All clear'}
      </span>
      <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
        {['stock', 'forex', 'crypto'].map((ac) => (
          <span key={ac} className="pill">
            <span className={`pill-dot ${risk[`${ac}_halted`] ? '' : 'off'}`} style={{ background: risk[`${ac}_halted`] ? 'var(--danger)' : 'var(--accent)' }} />
            {ac}
          </span>
        ))}
      </div>
    </div>
  );
}
