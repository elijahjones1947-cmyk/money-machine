import { useEffect, useState } from 'react';
import { useDashboard } from '../hooks/useDashboard.js';
import { api } from '../api.js';

const ASSET_CLASSES = ['stock', 'forex', 'crypto'];

function RiskRow({ assetClass, currentRiskPercent, currentMaxTrades, capPct }) {
  const [riskPercent, setRiskPercent] = useState(currentRiskPercent);
  const [maxTrades, setMaxTrades] = useState(currentMaxTrades);
  const [status, setStatus] = useState(null);

  useEffect(() => setRiskPercent(currentRiskPercent), [currentRiskPercent]);
  useEffect(() => setMaxTrades(currentMaxTrades), [currentMaxTrades]);

  const overCap = capPct != null && Number(riskPercent) > capPct;

  const save = async () => {
    setStatus('saving');
    try {
      await api.updateSettings({ asset_class: assetClass, risk_percent: Number(riskPercent), max_trades_per_day: Number(maxTrades) });
      setStatus('saved');
      setTimeout(() => setStatus(null), 1500);
    } catch (e) {
      setStatus(e.message);
    }
  };

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '12px 0', borderBottom: '1px solid var(--border)' }}>
      <div style={{ width: 70, textTransform: 'capitalize', fontWeight: 600 }}>{assetClass}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Risk % per trade</label>
        <input
          type="number" step="0.5" value={riskPercent}
          onChange={(e) => setRiskPercent(e.target.value)}
          style={{ width: 90, background: 'var(--bg)', border: `1px solid ${overCap ? 'var(--danger)' : 'var(--border)'}`, color: 'var(--text-primary)', padding: '6px 8px', borderRadius: 8 }}
        />
        {capPct != null && (
          <span style={{ fontSize: 11, color: overCap ? 'var(--danger)' : 'var(--text-muted)' }}>
            {overCap ? `Above the ${capPct}% risk-manager cap — every trade will be rejected` : `cap: ${capPct}%`}
          </span>
        )}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Max trades/day</label>
        <input
          type="number" value={maxTrades}
          onChange={(e) => setMaxTrades(e.target.value)}
          style={{ width: 90, background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text-primary)', padding: '6px 8px', borderRadius: 8 }}
        />
      </div>
      <button className="button button-accent" onClick={save} disabled={status === 'saving'}>
        {status === 'saved' ? 'Saved' : status === 'saving' ? 'Saving…' : 'Save'}
      </button>
      {status && status !== 'saving' && status !== 'saved' && <span className="error-text">{status}</span>}
    </div>
  );
}

function WatchlistRow({ assetClass, symbols, onAdded }) {
  const [symbol, setSymbol] = useState('');
  const [status, setStatus] = useState(null);

  const add = async (e) => {
    e.preventDefault();
    if (!symbol.trim()) return;
    setStatus('saving');
    try {
      await api.addWatchlist(symbol.trim().toUpperCase(), assetClass);
      setSymbol('');
      setStatus(null);
      onAdded();
    } catch (err) {
      setStatus(err.message);
    }
  };

  return (
    <div style={{ padding: '12px 0', borderBottom: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ width: 70, textTransform: 'capitalize', fontWeight: 600 }}>{assetClass}</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {symbols.length === 0 ? <span className="empty-state" style={{ padding: 0 }}>none watched</span> :
            symbols.map((s) => <span key={s} className="pill">{s}</span>)}
        </div>
      </div>
      <form onSubmit={add} style={{ display: 'flex', gap: 8, marginTop: 8, marginLeft: 82 }}>
        <input
          value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="Add symbol…"
          style={{ width: 140, background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text-primary)', padding: '6px 8px', borderRadius: 8 }}
        />
        <button className="button" type="submit">Add</button>
        {status && <span className="error-text">{status}</span>}
      </form>
    </div>
  );
}

export function Settings() {
  const { data, loading, refetch } = useDashboard();

  if (loading || !data) return <div className="empty-state">Loading…</div>;

  return (
    <div style={{ maxWidth: 560 }}>
      <h2>Settings</h2>

      <h3 style={{ marginTop: 24, marginBottom: 4 }}>Risk & sizing</h3>
      <div style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 8 }}>
        Risk % controls position sizing. If it's set above the risk manager's cap, every trade for that
        asset class gets silently rejected — this is exactly what caused a 2-day forex outage previously.
      </div>
      {ASSET_CLASSES.map((ac) => (
        <RiskRow
          key={ac}
          assetClass={ac}
          currentRiskPercent={data.risk_percent[ac]}
          currentMaxTrades={data.max_trades_per_day[ac]}
          capPct={data.risk_caps?.[ac] ? data.risk_caps[ac].max_position_size_pct * 100 : null}
        />
      ))}

      <h3 style={{ marginTop: 32, marginBottom: 4 }}>Watchlist</h3>
      {ASSET_CLASSES.map((ac) => (
        <WatchlistRow key={ac} assetClass={ac} symbols={data.watched_symbols[ac] || []} onAdded={refetch} />
      ))}
    </div>
  );
}
