import { useDashboard } from '../hooks/useDashboard.js';

export function RegimeDetail() {
  const { data, loading } = useDashboard();
  const regimes = data?.regimes ?? [];

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Market regime</h1>
          <div className="page-subtitle">Latest classified regime per watched symbol.</div>
        </div>
      </div>
      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : regimes.length === 0 ? (
        <div className="card"><div className="empty-state">No regime data yet</div></div>
      ) : (
        <div className="table-card">
          <table className="data-table">
            <thead><tr><th>Symbol</th><th>Asset class</th><th>Regime</th></tr></thead>
            <tbody>
              {regimes.map((r) => (
                <tr key={r.symbol}>
                  <td>{r.symbol}</td>
                  <td>{r.asset_class}</td>
                  <td><span className="regime-badge">{r.regime}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
