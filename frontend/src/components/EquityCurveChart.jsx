// Minimal hand-rolled SVG line chart — no charting library dependency
// for something this simple. Plots one or more equity curve series on
// a shared scale (index-based x-axis, since different symbols' curves
// have different point counts/timelines — this is about relative
// shape/drawdown comparison, not calendar alignment).
//
// `showArea` adds a soft gradient fill under each line (single-series
// use looks best). `showLabels` prints the first/last point's `time`
// value under the chart and the y-axis min/max to the left — off by
// default so existing callers (BacktestWidget/BacktestDetail) render
// exactly as before.
function formatLabel(time) {
  if (!time) return '';
  // Full ISO datetime -> short label. Falls back to the raw value for
  // anything that isn't ISO (e.g. backtest bar timestamps/indices).
  const d = new Date(time);
  if (isNaN(d.getTime())) return String(time);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function EquityCurveChart({ series, height = 160, width = 560, showArea = false, showLabels = false }) {
  const allEquities = series.flatMap((s) => s.points.map((p) => p.equity));
  if (allEquities.length === 0) return null;

  const min = Math.min(...allEquities);
  const max = Math.max(...allEquities);
  const pad = (max - min) * 0.1 || 1;
  const yMin = min - pad;
  const yMax = max + pad;

  const xOf = (i, len) => (i / Math.max(len - 1, 1)) * width;
  const yOf = (equity) => height - ((equity - yMin) / (yMax - yMin)) * height;

  const toPath = (points) =>
    points.length === 0
      ? ''
      : points
          .map((p, i) => `${i === 0 ? 'M' : 'L'}${xOf(i, points.length).toFixed(1)},${yOf(p.equity).toFixed(1)}`)
          .join(' ');

  const toAreaPath = (points) => {
    if (points.length === 0) return '';
    const line = toPath(points);
    const lastX = xOf(points.length - 1, points.length).toFixed(1);
    return `${line} L${lastX},${height} L0,${height} Z`;
  };

  const longestSeries = series.reduce((a, b) => (a.points.length >= b.points.length ? a : b));
  const firstTime = longestSeries.points[0]?.time;
  const lastTime = longestSeries.points[longestSeries.points.length - 1]?.time;

  return (
    <div>
      <div style={{ display: 'flex', gap: 8 }}>
        {showLabels && (
          <div className="chart-y-labels" style={{ height }}>
            <span>${Math.round(yMax).toLocaleString()}</span>
            <span>${Math.round(yMin).toLocaleString()}</span>
          </div>
        )}
        <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none">
          <defs>
            {series.map((s) => (
              <linearGradient key={`grad-${s.name}`} id={`grad-${s.name.replace(/[^a-zA-Z0-9]/g, '')}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={s.color} stopOpacity="0.35" />
                <stop offset="100%" stopColor={s.color} stopOpacity="0" />
              </linearGradient>
            ))}
          </defs>
          <line x1="0" y1={height} x2={width} y2={height} stroke="var(--border)" strokeWidth="1" />
          {showArea &&
            series.map((s) => (
              <path
                key={`area-${s.name}`}
                d={toAreaPath(s.points)}
                fill={`url(#grad-${s.name.replace(/[^a-zA-Z0-9]/g, '')})`}
                stroke="none"
              />
            ))}
          {series.map((s) => (
            <path key={s.name} d={toPath(s.points)} fill="none" stroke={s.color} strokeWidth="2" />
          ))}
        </svg>
      </div>
      {showLabels && (
        <div className="chart-x-labels" style={{ marginLeft: showLabels ? 56 : 0 }}>
          <span>{formatLabel(firstTime)}</span>
          <span>{formatLabel(lastTime)}</span>
        </div>
      )}
    </div>
  );
}
