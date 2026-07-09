// Minimal hand-rolled SVG line chart — no charting library dependency
// for something this simple. Plots one or more equity curve series on
// a shared scale (index-based x-axis, since different symbols' curves
// have different point counts/timelines — this is about relative
// shape/drawdown comparison, not calendar alignment).
export function EquityCurveChart({ series, height = 160, width = 560 }) {
  const allEquities = series.flatMap((s) => s.points.map((p) => p.equity));
  if (allEquities.length === 0) return null;

  const min = Math.min(...allEquities);
  const max = Math.max(...allEquities);
  const pad = (max - min) * 0.1 || 1;
  const yMin = min - pad;
  const yMax = max + pad;

  const toPath = (points) => {
    if (points.length === 0) return '';
    return points
      .map((p, i) => {
        const x = (i / Math.max(points.length - 1, 1)) * width;
        const y = height - ((p.equity - yMin) / (yMax - yMin)) * height;
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');
  };

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none">
      <line x1="0" y1={height} x2={width} y2={height} stroke="var(--border)" strokeWidth="1" />
      {series.map((s) => (
        <path key={s.name} d={toPath(s.points)} fill="none" stroke={s.color} strokeWidth="2" />
      ))}
    </svg>
  );
}
