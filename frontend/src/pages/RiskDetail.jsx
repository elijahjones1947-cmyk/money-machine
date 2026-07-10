import { useDashboard } from '../hooks/useDashboard.js';

export function RiskDetail() {
  const { data, loading } = useDashboard();
  const risk = data?.risk_state;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Risk state</h1>
          <div className="page-subtitle">Live halt status per asset class and account-wide.</div>
        </div>
      </div>
      {loading || !risk ? (
        <div className="empty-state">Loading…</div>
      ) : (
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 480 }}>
          <div className="pill" style={{ width: 'fit-content' }}>
            <span className="pill-dot" style={{ background: risk.account_halted ? 'var(--danger)' : 'var(--accent)' }} />
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
          {risk.daily_pnl && (
            <div style={{ marginTop: 4 }}>
              <div className="section-title" style={{ marginBottom: 8 }}>Today's P&amp;L by asset class</div>
              {['stock', 'forex', 'crypto'].map((ac) => (
                <div key={ac} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 4 }}>
                  <span style={{ textTransform: 'capitalize', color: 'var(--text-secondary)' }}>{ac}</span>
                  <span style={{ color: risk.daily_pnl[ac] >= 0 ? 'var(--accent)' : 'var(--danger)', fontWeight: 600 }}>
                    {risk.daily_pnl[ac] >= 0 ? '+' : ''}${risk.daily_pnl[ac]}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
