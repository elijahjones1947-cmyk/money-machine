import { useEffect, useState } from 'react';
import { useDashboard } from '../hooks/useDashboard.js';
import { api } from '../api.js';

const ASSET_CLASSES = ['stock', 'forex', 'crypto'];

// Each strategy family has its own param shape -- the create form switches
// its numeric/boolean fields and defaults based on which family is
// selected, rather than assuming Higher High Breakout's fields like it
// used to. Keys/defaults for Kev's ICC match pinescript/kevs_icc_strategy.pine's
// input.*() defaults (the forex/base tuning -- eqTolerancePct 0.05,
// slBufferPct 0.05; the Stock (0.10/0.15) and Crypto (0.20/0.35) variants
// are entered by hand when creating those strategy rows).
const STRATEGY_FAMILIES = {
  'Higher High Breakout': {
    numericFields: [
      { key: 'lookback', label: 'Lookback (bars)', step: '1' },
      { key: 'breakout_buffer_pct', label: 'Breakout buffer %', step: '0.01' },
      { key: 'ema_fast_length', label: 'EMA fast', step: '1' },
      { key: 'ema_slow_length', label: 'EMA slow', step: '1' },
      { key: 'take_profit_pct', label: 'Take profit %', step: '0.05' },
      { key: 'stop_loss_pct', label: 'Stop loss %', step: '0.05' },
      { key: 'rsi_length', label: 'RSI length', step: '1' },
      { key: 'rsi_min', label: 'RSI min', step: '1' },
    ],
    boolFields: [
      { key: 'use_rsi_filter', label: 'Use RSI filter' },
    ],
    defaultParams: {
      lookback: 7, breakout_buffer_pct: 0.05, ema_fast_length: 9, ema_slow_length: 21,
      take_profit_pct: 0.6, stop_loss_pct: 0.35, use_rsi_filter: true, rsi_length: 14, rsi_min: 45,
    },
    namePlaceholder: 'e.g. Higher High Breakout - Stock Tight',
  },
  "Kev's ICC": {
    numericFields: [
      { key: 'pivotLenL', label: 'Pivot lookback (left)', step: '1' },
      { key: 'pivotLenR', label: 'Pivot lookahead (right)', step: '1' },
      { key: 'eqTolerancePct', label: 'Equal high/low tolerance %', step: '0.01' },
      { key: 'dailyPivotLenL', label: 'Daily pivot lookback (left)', step: '1' },
      { key: 'dailyPivotLenR', label: 'Daily pivot lookahead (right)', step: '1' },
      { key: 'dailySwingsForBias', label: 'Daily swings for bias', step: '1' },
      { key: 'slBufferPct', label: 'SL buffer %', step: '0.01' },
      { key: 'h4PivotHistoryBars', label: '4H pivot history bars', step: '1' },
    ],
    boolFields: [
      { key: 'useDailyFilter', label: 'Use daily trend filter' },
      { key: 'blockOnIndecision', label: 'Block on indecision' },
    ],
    defaultParams: {
      pivotLenL: 5, pivotLenR: 5, eqTolerancePct: 0.05, useDailyFilter: true,
      dailyPivotLenL: 3, dailyPivotLenR: 3, dailySwingsForBias: 3, slBufferPct: 0.05,
      h4PivotHistoryBars: 10, blockOnIndecision: true,
    },
    namePlaceholder: "e.g. Kev's ICC (Stock)",
  },
};

// Each family's own shape gets the tailored one-liner below; anything
// unrecognized (a param shape from neither family, e.g. hand-edited)
// falls back to a generic key:value listing instead of silently printing
// "undefined" for keys that shape doesn't have.
const HHB_SUMMARY_KEYS = ['lookback', 'breakout_buffer_pct', 'take_profit_pct', 'stop_loss_pct'];
const ICC_SUMMARY_KEYS = ['pivotLenL', 'pivotLenR', 'eqTolerancePct', 'slBufferPct'];

function paramsSummary(params) {
  if (!params) return '';
  if (HHB_SUMMARY_KEYS.every((k) => k in params)) {
    return `lookback ${params.lookback} · buffer ${params.breakout_buffer_pct}% · TP ${params.take_profit_pct}% · SL ${params.stop_loss_pct}%`;
  }
  if (ICC_SUMMARY_KEYS.every((k) => k in params)) {
    return `pivot ${params.pivotLenL}/${params.pivotLenR} · eq tol ${params.eqTolerancePct}% · SL buffer ${params.slBufferPct}%${params.useDailyFilter === false ? ' · daily filter off' : ''}`;
  }
  const entries = Object.entries(params);
  if (entries.length === 0) return '';
  return entries.map(([k, v]) => `${k}: ${v}`).join(' · ');
}

function CreateStrategyForm({ onCreated }) {
  const familyNames = Object.keys(STRATEGY_FAMILIES);
  const [familyName, setFamilyName] = useState(familyNames[0]);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [params, setParams] = useState(STRATEGY_FAMILIES[familyNames[0]].defaultParams);
  const [status, setStatus] = useState(null);
  const [open, setOpen] = useState(false);

  const family = STRATEGY_FAMILIES[familyName];
  const setParam = (key, value) => setParams((p) => ({ ...p, [key]: value }));

  const changeFamily = (nextFamilyName) => {
    setFamilyName(nextFamilyName);
    setParams(STRATEGY_FAMILIES[nextFamilyName].defaultParams);
  };

  const create = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setStatus('saving');
    try {
      const numericParams = { ...params };
      for (const f of family.numericFields) numericParams[f.key] = Number(params[f.key]);
      await api.createStrategy(name.trim(), numericParams, description.trim() || undefined);
      setName('');
      setDescription('');
      setParams(family.defaultParams);
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
            <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Strategy family</label>
            <select
              value={familyName} onChange={(e) => changeFamily(e.target.value)}
              style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text-primary)', padding: '6px 8px', borderRadius: 8 }}
            >
              {familyNames.map((fn) => <option key={fn} value={fn}>{fn}</option>)}
            </select>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1, minWidth: 180 }}>
            <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Name</label>
            <input
              value={name} onChange={(e) => setName(e.target.value)} placeholder={family.namePlaceholder}
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
          {family.numericFields.map((f) => (
            <div key={f.key} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{f.label}</label>
              <input
                type="number" step={f.step} value={params[f.key]}
                onChange={(e) => setParam(f.key, e.target.value)}
                style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text-primary)', padding: '6px 8px', borderRadius: 8, width: '100%' }}
              />
            </div>
          ))}
          {family.boolFields.map((f) => (
            <div key={f.key} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <input
                type="checkbox" checked={!!params[f.key]}
                onChange={(e) => setParam(f.key, e.target.checked)}
              />
              <label style={{ fontSize: 12 }}>{f.label}</label>
            </div>
          ))}
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
