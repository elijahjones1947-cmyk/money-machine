import { useDashboard } from '../hooks/useDashboard.js';

export function PositionsDetail() {
  const { data, loading } = useDashboard();
  const positions = data?.positions ?? [];

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Positions</h1>
          <div className="page-subtitle">Currently open positions across both brokers.</div>
        </div>
      </div>
      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : positions.length === 0 ? (
        <div className="card"><div className="empty-state">No open positions</div></div>
      ) : (
        <div className="table-card">
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
                  <td style={{ color: p.unrealized_pl >= 0 ? 'var(--accent)' : 'var(--danger)', fontWeight: 600 }}>
                    {p.unrealized_pl >= 0 ? '+' : ''}{p.unrealized_pl}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
