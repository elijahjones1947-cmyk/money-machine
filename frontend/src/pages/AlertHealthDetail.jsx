import { useEffect, useMemo, useState } from 'react';
import { useDashboard } from '../hooks/useDashboard.js';
import { api } from '../api.js';

function timeAgo(iso) {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (isNaN(then)) return null;
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

// Current status (per /api/dashboard's alert_health, in-memory,
// resets on a restart) alongside a real per-symbol HISTORY pulled from
// /api/webhook_signals -- durable across restarts, but only ever shows
// AUTHENTICATED signals (bad-secret/malformed hits never reach the
// durable queue -- see db.get_recent_webhook_signals's own docstring).
export function AlertHealthDetail() {
  const { data, loading: dashboardLoading } = useDashboard();
  const [signals, setSignals] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.listWebhookSignals(200).then((r) => setSignals(r.signals)).catch((e) => setError(e.message));
  }, []);

  const alertHealth = data?.alert_health ?? [];

  const byClass = useMemo(() => {
    const grouped = { stock: [], forex: [], crypto: [] };
    for (const h of alertHealth) {
      (grouped[h.asset_class] ??= []).push(h);
    }
    return grouped;
  }, [alertHealth]);

  const signalsBySymbol = useMemo(() => {
    const grouped = new Map();
    for (const s of signals || []) {
      if (!grouped.has(s.symbol)) grouped.set(s.symbol, []);
      grouped.get(s.symbol).push(s);
    }
    return grouped;
  }, [signals]);

  const loading = dashboardLoading || signals === null;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Alert health</h1>
          <div className="page-subtitle">
            Current webhook-silence status per watched symbol, plus its recent alert history (last {200} authenticated
            signals across the whole watchlist).
          </div>
        </div>
      </div>

      {error && <div className="error-text" style={{ marginBottom: 12 }}>{error}</div>}

      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : (
        Object.entries(byClass).filter(([, rows]) => rows.length > 0).map(([ac, rows]) => (
          <div className="section" key={ac}>
            <div className="section-title">{ac}</div>
            <div className="regime-grid">
              {rows.map((h) => {
                const history = signalsBySymbol.get(h.symbol) || [];
                return (
                  <div
                    key={h.symbol}
                    className="regime-card"
                    style={{
                      borderColor: h.stale ? 'var(--danger)' : 'var(--accent)',
                      background: h.stale ? 'var(--danger-dim)' : 'var(--accent-dim)',
                    }}
                  >
                    <div className="regime-card-top">
                      <div>
                        <div className="regime-card-symbol">{h.symbol}</div>
                        <div className="regime-card-class">
                          {h.last_webhook_at ? `Last alert ${timeAgo(h.last_webhook_at)}` : 'No alert ever received'}
                        </div>
                      </div>
                      <span
                        className="regime-card-badge"
                        style={{ background: h.stale ? 'var(--danger)' : 'var(--accent)' }}
                      >
                        {h.stale ? 'Stale' : 'Fresh'}
                      </span>
                    </div>

                    <div className="alert-health-history" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                      {history.length === 0 ? (
                        <div className="empty-state" style={{ padding: 0, fontSize: 12 }}>No alerts recorded yet</div>
                      ) : (
                        history.slice(0, 15).map((s) => (
                          <div
                            key={s.id}
                            style={{
                              display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 12,
                              borderBottom: '1px solid var(--border)', paddingBottom: 4,
                            }}
                          >
                            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                              <span className={`action-badge ${s.action === 'sell' ? 'sell' : 'buy'}`}>{s.action}</span>
                              {s.manual_flag && <span style={{ color: 'var(--text-muted)' }}>manual</span>}
                              {s.status === 'failed' && (
                                <span style={{ color: 'var(--danger)' }} title={s.error_message || 'failed'}>failed</span>
                              )}
                            </span>
                            <span style={{ color: 'var(--text-secondary)' }}>
                              {new Date(s.received_at).toLocaleString(undefined, {
                                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
                              })}
                            </span>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))
      )}
    </div>
  );
}
