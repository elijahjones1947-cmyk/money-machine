import { useEffect, useState } from 'react';
import { useDashboard } from '../hooks/useDashboard.js';
import { api } from '../api.js';
import './Settings.css';

const ASSET_CLASSES = ['stock', 'forex', 'crypto'];

function Field({ label, value, onChange, borderColor }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{label}</label>
      <input
        type="number" step="0.5" value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{ background: 'var(--bg)', border: `1px solid ${borderColor || 'var(--border)'}`, color: 'var(--text-primary)', padding: '6px 8px', borderRadius: 8, width: '100%' }}
      />
    </div>
  );
}

// One card per asset class covering every risk lever the bot actually
// enforces -- previously only risk_percent/max_trades_per_day were
// editable here; max_position_size_pct (the cap), max_daily_loss_pct,
// max_open_positions, max_leverage, and safety_stop_loss_pct lived only
// in hardcoded config.py, invisible from the dashboard and only
// changeable by editing code and redeploying. That split (one visible
// number, several invisible ones) is what let risk_percent silently
// drift above the cap for two days before anyone noticed.
function RiskCard({ assetClass, data, onSaved }) {
  const caps = data.risk_caps?.[assetClass] || {};
  const toPct = (v) => (v != null ? v * 100 : '');

  const [riskPercent, setRiskPercent] = useState(data.risk_percent[assetClass]);
  const [maxTrades, setMaxTrades] = useState(data.max_trades_per_day[assetClass]);
  const [maxPositionPct, setMaxPositionPct] = useState(toPct(caps.max_position_size_pct));
  const [maxDailyLossPct, setMaxDailyLossPct] = useState(toPct(caps.max_daily_loss_pct));
  const [maxOpenPositions, setMaxOpenPositions] = useState(caps.max_open_positions ?? '');
  const [safetyStopPct, setSafetyStopPct] = useState(toPct(caps.safety_stop_loss_pct));
  const [maxLeverage, setMaxLeverage] = useState(caps.max_leverage ?? '');
  const [status, setStatus] = useState(null);

  useEffect(() => {
    const c = data.risk_caps?.[assetClass] || {};
    setRiskPercent(data.risk_percent[assetClass]);
    setMaxTrades(data.max_trades_per_day[assetClass]);
    setMaxPositionPct(toPct(c.max_position_size_pct));
    setMaxDailyLossPct(toPct(c.max_daily_loss_pct));
    setMaxOpenPositions(c.max_open_positions ?? '');
    setSafetyStopPct(toPct(c.safety_stop_loss_pct));
    setMaxLeverage(c.max_leverage ?? '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, assetClass]);

  const overCap = maxPositionPct !== '' && Number(riskPercent) > Number(maxPositionPct);

  const save = async () => {
    setStatus('saving');
    try {
      const payload = {
        asset_class: assetClass,
        risk_percent: Number(riskPercent),
        max_trades_per_day: Number(maxTrades),
        max_position_size_pct: Number(maxPositionPct),
        max_daily_loss_pct: Number(maxDailyLossPct),
        max_open_positions: Number(maxOpenPositions),
        safety_stop_loss_pct: Number(safetyStopPct),
      };
      if (assetClass === 'forex') payload.max_leverage = Number(maxLeverage);
      await api.updateSettings(payload);
      setStatus('saved');
      onSaved?.();
      setTimeout(() => setStatus(null), 1500);
    } catch (e) {
      setStatus(e.message);
    }
  };

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <span style={{ textTransform: 'capitalize', fontWeight: 700, fontSize: 15 }}>{assetClass}</span>
        <button className="button button-accent" onClick={save} disabled={status === 'saving'}>
          {status === 'saved' ? 'Saved' : status === 'saving' ? 'Saving…' : 'Save'}
        </button>
      </div>
      {overCap && (
        <div style={{ fontSize: 12, color: 'var(--danger)', marginBottom: 10 }}>
          Risk % ({riskPercent}%) is above the position-size cap ({maxPositionPct}%) — trades get sized down to
          the cap automatically now, not rejected outright.
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12 }}>
        <Field label="Risk % per trade" value={riskPercent} onChange={setRiskPercent} borderColor={overCap ? 'var(--danger)' : undefined} />
        <Field label="Max trades/day" value={maxTrades} onChange={setMaxTrades} />
        <Field label="Position size cap %" value={maxPositionPct} onChange={setMaxPositionPct} borderColor={overCap ? 'var(--danger)' : undefined} />
        <Field label="Max daily loss %" value={maxDailyLossPct} onChange={setMaxDailyLossPct} />
        <Field label="Max open positions" value={maxOpenPositions} onChange={setMaxOpenPositions} />
        <Field label="Safety stop-loss %" value={safetyStopPct} onChange={setSafetyStopPct} />
        {assetClass === 'forex' && <Field label="Max leverage" value={maxLeverage} onChange={setMaxLeverage} />}
      </div>
      {status && status !== 'saving' && status !== 'saved' && <div className="error-text" style={{ marginTop: 8 }}>{status}</div>}
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
      <form onSubmit={add} className="watchlist-add-form">
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
    <div style={{ maxWidth: 720 }}>
      <div className="page-header">
        <div>
          <h1>Settings</h1>
          <div className="page-subtitle">Risk sizing and watchlist, per asset class.</div>
        </div>
      </div>

      <div className="section">
        <div className="section-title">Risk &amp; sizing</div>
        <div className="page-subtitle" style={{ marginBottom: 12 }}>
          Every risk lever the bot actually enforces is editable here now — position size cap, max daily loss,
          max open positions, leverage (forex), and the independent safety stop-loss backstop — not just the
          sizing percentage. "Safety stop-loss %" is a last-resort auto-exit if a position's unrealized loss
          blows through this threshold, independent of whether a strategy exit signal ever arrives.
        </div>
        {ASSET_CLASSES.map((ac) => (
          <RiskCard key={ac} assetClass={ac} data={data} onSaved={refetch} />
        ))}
      </div>

      <div className="section">
        <div className="section-title">Watchlist</div>
        <div className="card">
          {ASSET_CLASSES.map((ac) => (
            <WatchlistRow key={ac} assetClass={ac} symbols={data.watched_symbols[ac] || []} onAdded={refetch} />
          ))}
        </div>
      </div>
    </div>
  );
}
