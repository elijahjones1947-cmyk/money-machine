import { useEffect, useState } from 'react';
import { useDashboard } from '../hooks/useDashboard.js';
import { api } from '../api.js';

const ASSET_CLASSES = ['stock', 'forex', 'crypto'];

const PARAM_FIELDS = [
  { key: 'lookback', label: 'Lookback (bars)', step: '1' },
  { key: 'breakout_buffer_pct', label: 'Breakout buffer %', step: '0.01' },
  { key: 'ema_fast_length', label: 'EMA fast', step: '1' },
  { key: 'ema_slow_length', label: 'EMA slow', step: '1' },
  { key: 'take_profit_pct', label: 'Take profit %', step: '0.05' },
  { key: 'stop_loss_pct', label: 'Stop loss %', step: '0.05' },
  { key: 'rsi_length', label: 'RSI length', step: '1' },
  { key: 'rsi_min', label: 'RSI min', step: '1' },
];

const DEFAULT_PARAMS = {
  lookback: 7, breakout_buffer_pct: 0.05, ema_fast_length: 9, ema_slow_length: 21,
  take_profit_pct: 0.6, stop_loss_pct: 0.35, use_rsi_filter: true, rsi_length: 14, rsi_min: 45,
};

function paramsSummary(params) {
  if (!params) return '';
  return `lookback ${params.lookback} · buffer ${params.breakout_buffer_pct}% · TP ${params.take_profit_pct}% · SL ${params.stop_loss_pct}%`;
}

function CreateStrategyForm({ onCreated }) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [params, setParams] = useState(DEFAULT_PARAMS);
  const [status, setStatus] = useState(null);
  const [open, setOpen] = useState(false);

  const setParam = (key, value) => setParams((p) => ({ ...p, [key]: value }));

  const create = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setStatus('saving');
    try {
      const numericParams = { ...params };
      for (const f of PARAM_FIELDS) numericParams[f.key] = Number(params[f.key]);
      await api.createStrategy(name.trim(), numericParams, description.trim() || undefined);
      setName('');
      setDescription('');
      setParams(DEFAULT_PARAMS);
      setStatus(null);
      setOpen(false);
      onCreated();
    } catch (err) {
      setStatus(err.message);
    }
  };

  if (!open) {
    return (
      <button className="button button-accent" onClick={() => setOpen(true)}>
        + New strategy
      </button>
    );
  }

  return (
    <form onSubmit={create} className="card" style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1, minWidth: 180 }}>
            <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Name</label>
            <input
              value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Higher High Breakout - Stock Tight"
              style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text-primary)', padding: '6px 8px', borderRadius: 8 }}
            />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1, minWidth: 180 }}>
            <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Description (optional)</label>
            <input
              value={description} onChange={(e) => setDescription(e.target.value)}
              style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text-primary)', padding: '6px 8px', borderRadius: 8 }}
            />
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 12 }}>
          {PARAM_FIELDS.map((f) => (
            <div key={f.key} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{f.label}</label>
              <input
                type="number" step={f.step} value={params[f.key]}
                onChange={(e) => setParam(f.key, e.target.value)}
                style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text-primary)', padding: '6px 8px', borderRadius: 8, width: '100%' }}
              />
            </div>
          ))}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <input
              type="checkbox" checked={params.use_rsi_filter}
              onChange={(e) => setParam('use_rsi_filter', e.target.checked)}
            />
            <label style={{ fontSize: 12 }}>Use RSI filter</label>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button className="button button-accent" type="submit" disabled={status === 'saving'}>
            {status === 'saving' ? 'Creating…' : 'Create strategy'}
          </button>
          <button className="button" type="button" onClick={() => setOpen(false)}>Cancel</button>
          {status && status !== 'saving' && <span className="error-text">{status}</span>}
        </div>
      </div>
    </form>
  );
}

function AssignmentRow({ symbol, assetClass, assignment, strategies, openPositionSymbols, onSwitched }) {
  const [selectedId, setSelectedId] = useState(assignment?.id ?? '');
  const [confirming, setConfirming] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    setSelectedId(assignment?.id ?? '');
  }, [assignment]);

  const hasOpenPosition = openPositionSymbols.has(symbol);
  const isChanged = selectedId !== '' && Number(selectedId) !== assignment?.id;

  const doSwitch = async () => {
    setSwitching(true);
    setError(null);
    try {
      await api.assignStrategy(symbol, Number(selectedId));
      setConfirming(false);
      onSwitched();
    } catch (e) {
      setError(e.message);
    } finally {
      setSwitching(false);
    }
  };

  return (
    <tr>
      <td>{symbol}</td>
      <td>{assetClass}</td>
      <td>
        {assignment ? (
          <span title={paramsSummary(assignment.params)}>{assignment.name} v{assignment.version}</span>
        ) : (
          <span className="empty-state" style={{ padding: 0 }}>none assigned</span>
        )}
      </td>
      <td>{hasOpenPosition ? <span className="pill">open position</span> : '—'}</td>
      <td>
        {switching ? (
          <button className="button" disabled>Switching…</button>
        ) : confirming ? (
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: hasOpenPosition ? 'var(--danger)' : 'var(--text-secondary)' }}>
              {hasOpenPosition ? 'Will force-close the open position first.' : 'Confirm switch?'}
            </span>
            <button className="button button-danger" onClick={doSwitch}>Confirm</button>
            <button className="button" onClick={() => setConfirming(false)}>Cancel</button>
          </div>
        ) : (
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <select
              value={selectedId}
              onChange={(e) => setSelectedId(e.target.value)}
              style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text-primary)', padding: '6px 8px', borderRadius: 8 }}
            >
              {!assignment && <option value="">— select —</option>}
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>{s.name} v{s.version}</option>
              ))}
            </select>
            <button className="button button-accent" disabled={!isChanged} onClick={() => setConfirming(true)}>
              Switch
            </button>
          </div>
        )}
        {error && <div className="error-text" style={{ marginTop: 4 }}>{error}</div>}
      </td>
    </tr>
  );
}

export function StrategiesDetail() {
  const { data: dashboardData } = useDashboard();
  const [strategies, setStrategies] = useState(null);
  const [assignments, setAssignments] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const refetch = async () => {
    try {
      const [s, a] = await Promise.all([api.listStrategies(), api.listStrategyAssignments()]);
      setStrategies(s.strategies);
      setAssignments(a.assignments);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const watchedSymbols = dashboardData?.watched_symbols || {};
  const openPositionSymbols = new Set((dashboardData?.positions || []).map((p) => p.symbol));
  const assignmentBySymbol = new Map((assignments || []).map((a) => [a.symbol, a]));

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Strategies</h1>
          <div className="page-subtitle">
            Named, versioned strategy definitions and each symbol's currently active one. Switching a symbol's
            strategy force-closes any open position for it first, then updates which strategy_id /webhook will
            accept for that symbol going forward.
          </div>
        </div>
      </div>

      {error && <div className="error-text" style={{ marginBottom: 12 }}>{error}</div>}

      <div className="section">
        <div className="section-title">Strategy definitions</div>
        <div style={{ marginBottom: 12 }}>
          <CreateStrategyForm onCreated={refetch} />
        </div>
        {loading ? (
          <div className="empty-state">Loading…</div>
        ) : !strategies || strategies.length === 0 ? (
          <div className="card"><div className="empty-state">No strategies created yet</div></div>
        ) : (
          <div className="table-card">
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr><th>Name</th><th>Version</th><th>Params</th><th>Description</th><th>Created</th></tr>
                </thead>
                <tbody>
                  {strategies.map((s) => (
                    <tr key={s.id}>
                      <td>{s.name}</td>
                      <td>v{s.version}</td>
                      <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{paramsSummary(s.params)}</td>
                      <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{s.description || '—'}</td>
                      <td>{s.created_at ? new Date(s.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      <div className="section">
        <div className="section-title">Symbol assignments</div>
        {loading ? (
          <div className="empty-state">Loading…</div>
        ) : (!strategies || strategies.length === 0) ? (
          <div className="card"><div className="empty-state">Create a strategy above before assigning one to a symbol</div></div>
        ) : (
          <div className="table-card">
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr><th>Symbol</th><th>Asset class</th><th>Active strategy</th><th>Position</th><th>Switch to</th></tr>
                </thead>
                <tbody>
                  {ASSET_CLASSES.flatMap((assetClass) =>
                    (watchedSymbols[assetClass] || []).map((symbol) => (
                      <AssignmentRow
                        key={symbol}
                        symbol={symbol}
                        assetClass={assetClass}
                        assignment={assignmentBySymbol.get(symbol)}
                        strategies={strategies}
                        openPositionSymbols={openPositionSymbols}
                        onSwitched={refetch}
                      />
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
