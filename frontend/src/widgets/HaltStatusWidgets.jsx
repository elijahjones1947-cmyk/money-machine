import { useDashboard } from '../hooks/useDashboard.js';

// Three small per-asset-class widgets replacing the single, taller
// "Halt status" card -- same underlying data (risk_state), just a
// smaller footprint: account_halted (the account-wide breaker) is
// folded into EACH one rather than shown separately, since it blocks
// trading for every class regardless of that class's own halt flag --
// hiding it here would make a card claim "clear" while the account is
// actually halted.
function HaltMini({ assetClass }) {
  const { data, loading } = useDashboard();
  const risk = data?.risk_state;

  if (loading) return <div className="empty-state">Loading…</div>;
  if (!risk) return <div className="empty-state">No data</div>;

  const halted = risk.account_halted || risk[`${assetClass}_halted`];

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, height: '100%' }}>
      <span style={{
        width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
        background: halted ? 'var(--danger)' : 'var(--accent)',
        boxShadow: halted ? '0 0 10px var(--danger-dim)' : '0 0 10px var(--accent-dim)',
      }} />
      <span className={`metric-value ${halted ? 'negative' : 'positive'}`} style={{ fontSize: 18 }}>
        {halted ? 'Halted' : 'Clear'}
      </span>
    </div>
  );
}

export function StockHaltWidget() { return <HaltMini assetClass="stock" />; }
export function ForexHaltWidget() { return <HaltMini assetClass="forex" />; }
export function CryptoHaltWidget() { return <HaltMini assetClass="crypto" />; }
