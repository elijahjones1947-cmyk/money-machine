// Visual-only paper/live indicator, styled as a sliding toggle. This is
// deliberately NOT a functional switch -- flipping live broker
// credentials from a casual dashboard control is too consequential
// (real money) for a one-click UI toggle. It always reflects
// config.TRADING_MODE (server-side, env-configured), never writes to it.
export function ModeToggle({ mode }) {
  const isLive = mode === 'live';
  return (
    <div
      className={`mode-toggle ${isLive ? 'mode-toggle-live' : 'mode-toggle-paper'}`}
      title="Trading mode is set server-side via TRADING_MODE — this reflects current state and can't be switched here"
    >
      <span className="mode-toggle-label">Paper</span>
      <span className="mode-toggle-label">Live</span>
      <div className="mode-toggle-thumb" />
    </div>
  );
}
