import { useDashboard } from '../hooks/useDashboard.js';

function formatSilentFor(seconds) {
  if (seconds == null) return 'never';
  const hours = seconds / 3600;
  if (hours < 1) return `${Math.round(seconds / 60)}m ago`;
  if (hours < 48) return `${hours.toFixed(1)}h ago`;
  return `${(hours / 24).toFixed(1)}d ago`;
}

// alert_health (per-symbol last_webhook_at + a `stale` flag) comes
// straight from /api/dashboard, computed against the EXACT same
// threshold and market-hours gate alerts.py's own Discord silence alert
// uses (alerts.WEBHOOK_SILENCE_THRESHOLD_SECONDS, alerts._is_market_hours)
// -- a symbol only shows stale here when it would also be Discord-alert-
// eligible, so this never contradicts what actually pages Eli.
export function AlertHealthWidget() {
  const { data, loading } = useDashboard();
  const health = data?.alert_health ?? [];

  if (loading) return <div className="empty-state">Loading…</div>;
  if (health.length === 0) return <div className="empty-state">No watched symbols</div>;

  const sorted = [...health].sort((a, b) => {
    if (a.stale !== b.stale) return a.stale ? -1 : 1;
    if (a.silent_for_seconds == null) return 1;
    if (b.silent_for_seconds == null) return -1;
    return b.silent_for_seconds - a.silent_for_seconds;
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {sorted.map((h) => (
        <div key={h.symbol} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 13 }}>
          <span>{h.symbol}</span>
          <span
            className="pill"
            style={h.stale ? { color: 'var(--danger)', borderColor: 'var(--danger)' } : undefined}
            title={h.stale ? 'No webhook while this symbol\'s market has been open longer than the alert-silence threshold' : undefined}
          >
            {formatSilentFor(h.silent_for_seconds)}
          </span>
        </div>
      ))}
    </div>
  );
}
