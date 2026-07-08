import { useDashboard } from '../hooks/useDashboard.js';

export function RiskDetail() {
  const { data, loading } = useDashboard();
  const risk = data?.risk_state;

  return (
    <div>
      <h2>Risk state</h2>
      {loading || !risk ? (
        <div className="empty-state">Loading…</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 480 }}>
          <div className="pill" style={{ width: 'fit-content' }}>
            <span className={`pill-dot ${risk.account_halted ? '' : 'off'}`} style={{ background: risk.account_halted ? 'var(--danger)' : 'var(--accent)' }} />
            Account-wide: {risk.account_halted ? 'HALTED' : 'normal'}
          </div>
          {['stock', 'forex', 'crypto'].map((ac) => (
            <div key={ac} style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border)', paddingBottom: 8 }}>
              <span style={{ textTransform: 'capitalize' }}>{ac}</span>
              <span style={{ color: risk[`${ac}_halted`] ? 'var(--danger)' : 'var(--accent)' }}>
                {risk[`${ac}_halted`] ? 'Halted (daily loss limit hit)' : 'Trading normally'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
