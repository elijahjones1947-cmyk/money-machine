import { useDashboard } from '../hooks/useDashboard.js';

export function RegimeDetail() {
  const { data, loading } = useDashboard();
  const regimes = data?.regimes ?? [];

  return (
    <div>
      <h2>Market regime</h2>
      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : regimes.length === 0 ? (
        <div className="empty-state">No regime data yet</div>
      ) : (
        <table className="data-table">
          <thead><tr><th>Symbol</th><th>Asset class</th><th>Regime</th></tr></thead>
          <tbody>
            {regimes.map((r) => (
              <tr key={r.symbol}>
                <td>{r.symbol}</td>
                <td>{r.asset_class}</td>
                <td>{r.regime}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
