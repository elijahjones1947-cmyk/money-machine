// Per-position trajectory visual: a two-state "heaven" (winning, gold/
// light/ascending) vs "hell" (losing, dark/ember/descending) motif.
// Deliberately built as this explicit two-state scaffold now rather than
// a full theme -- the "bible-type" texture gets layered on top of these
// heaven/hell class hooks later, so keep the class names semantic.
//
// There's no historical per-position price series stored anywhere
// (positions are fetched live from the broker each request, not
// snapshotted over time), so this isn't a multi-point chart -- it's a
// single entry-to-current trajectory, sized by % move relative to cost
// basis and directioned by profit/loss rather than raw price direction
// (so a profitable short still reads as "ascending").
export function TrajectoryBar({ avgEntry, currentPrice, unrealizedPl, qty }) {
  const entryNum = parseFloat(avgEntry);
  const currentNum = parseFloat(currentPrice);
  const hasPrices = !isNaN(entryNum) && !isNaN(currentNum);

  const costBasis = Math.abs(qty) * (isNaN(entryNum) ? 0 : entryNum);
  const pnlPct = costBasis > 0 ? (unrealizedPl / costBasis) * 100 : 0;
  const isHeaven = unrealizedPl >= 0;
  const magnitude = Math.min(100, Math.abs(pnlPct) * 8); // 12.5% move = full bar

  return (
    <div className={`trajectory ${isHeaven ? 'trajectory-heaven' : 'trajectory-hell'}`}>
      <div className="trajectory-track">
        <div className="trajectory-fill" style={{ width: `${Math.max(6, magnitude)}%` }} />
        <span className="trajectory-glyph">{isHeaven ? '↑' : '↓'}</span>
      </div>
      <div className="trajectory-caption">
        <span>{isHeaven ? 'Ascending' : 'Falling'}</span>
        {hasPrices && <span>{pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%</span>}
      </div>
    </div>
  );
}
