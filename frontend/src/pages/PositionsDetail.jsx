import { useDashboard } from '../hooks/useDashboard.js';

export function PositionsDetail() {
  const { data, loading } = useDashboard();
  const positions = data?.positions ?? [];

  return (
    <div>
      <h2>Positions</h2>
      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : positions.length === 0 ? (
        <div className="empty-state">No open positions</div>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Symbol</th><th>Asset class</th><th>Qty</th><th>Avg entry</th><th>Current price</th><th>Unrealized P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p, i) => (
              <tr key={i}>
                <td>{p.symbol}</td>
                <td>{p.asset_class}</td>
                <td>{p.qty}</td>
                <td>{p.avg_entry}</td>
                <td>{p.current_price}</td>
                <td style={{ color: p.unrealized_pl >= 0 ? 'var(--accent)' : 'var(--danger)' }}>
                  {p.unrealized_pl >= 0 ? '+' : ''}{p.unrealized_pl}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
