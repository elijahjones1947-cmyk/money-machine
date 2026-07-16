import { useState } from 'react';
import { useDashboard } from '../hooks/useDashboard.js';
import { api } from '../api.js';

export function PositionsDetail() {
  const { data, loading, refetch } = useDashboard();
  const positions = data?.positions ?? [];

  // Which row is mid-confirm ("Close" clicked, awaiting "Confirm"/"Cancel"),
  // which row has a close request in flight, and the last close error (if
  // any) -- all keyed by symbol so multiple rows never interfere with each
  // other. Only one of confirming/closing is ever set for a given symbol
  // at a time.
  const [confirmingSymbol, setConfirmingSymbol] = useState(null);
  const [closingSymbol, setClosingSymbol] = useState(null);
  const [closeError, setCloseError] = useState(null); // { symbol, message }

  const requestClose = (symbol) => {
    setCloseError(null);
    setConfirmingSymbol(symbol);
  };

  const cancelClose = () => setConfirmingSymbol(null);

  const confirmClose = async (symbol) => {
    setConfirmingSymbol(null);
    setClosingSymbol(symbol);
    setCloseError(null);
    try {
      await api.manualClose(symbol);
      await refetch();
    } catch (e) {
      setCloseError({ symbol, message: e.message });
    } finally {
      setClosingSymbol(null);
    }
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Positions</h1>
          <div className="page-subtitle">Currently open positions across both brokers.</div>
        </div>
      </div>
      {closeError && (
        <div className="error-text" style={{ marginBottom: 12 }}>
          Could not close {closeError.symbol}: {closeError.message}
        </div>
      )}
      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : positions.length === 0 ? (
        <div className="card"><div className="empty-state">No open positions</div></div>
      ) : (
        <div className="table-card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Symbol</th><th>Asset class</th><th>Qty</th><th>Avg entry</th><th>Current price</th><th>Unrealized P&amp;L</th><th>Manual close</th>
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
                  <td>
                    {closingSymbol === p.symbol ? (
                      <button className="button" disabled>Closing…</button>
                    ) : confirmingSymbol === p.symbol ? (
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button className="button button-danger" onClick={() => confirmClose(p.symbol)}>
                          Confirm
                        </button>
                        <button className="button" onClick={cancelClose}>Cancel</button>
                      </div>
                    ) : (
                      <button className="button button-danger" onClick={() => requestClose(p.symbol)}>
                        Close
                      </button>
                    )}
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
