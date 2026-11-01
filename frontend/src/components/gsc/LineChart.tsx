interface Point {
  label: string;
  value: number | null;
}

interface LineChartProps {
  title: string;
  unit: string;
  points: Point[];
  /** Draw small values at the top (average position: lower is better). */
  invert?: boolean;
  digits?: number;
}

const W = 640;
const H = 180;
const PAD = { left: 48, right: 12, top: 12, bottom: 26 };

/**
 * A plain SVG line of stored values. Days without a value are gaps, never zeros, and
 * nothing is drawn when there are fewer than two points.
 */
export function LineChart({ title, unit, points, invert = false, digits = 0 }: LineChartProps) {
  const known = points.filter((p): p is { label: string; value: number } => p.value !== null);
  if (known.length < 2) {
    return (
      <figure className="chart">
        <figcaption>{title}</figcaption>
        <p className="form-hint">Not enough stored days to draw a chart.</p>
      </figure>
    );
  }
  const min = invert ? Math.min(...known.map((p) => p.value)) : 0;
  const max = Math.max(...known.map((p) => p.value));
  const span = max - min || 1;
  const x = (i: number) => PAD.left + (i / (points.length - 1)) * (W - PAD.left - PAD.right);
  const y = (v: number) => {
    const t = (v - min) / span;
    const frac = invert ? t : 1 - t;
    return PAD.top + frac * (H - PAD.top - PAD.bottom);
  };
  // Break the line at missing days.
  const segments: string[][] = [[]];
  points.forEach((p, i) => {
    if (p.value === null) segments.push([]);
    else segments[segments.length - 1].push(`${x(i).toFixed(1)},${y(p.value).toFixed(1)}`);
  });
  const fmt = (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: digits });
  const first = known[0];
  const last = known[known.length - 1];
  const summary = `${title}: ${fmt(first.value)} ${unit} on ${first.label} to ${fmt(last.value)} ${unit} on ${last.label}, highest ${fmt(max)}.`;

  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={summary} preserveAspectRatio="none">
        <line x1={PAD.left} x2={W - PAD.right} y1={H - PAD.bottom} y2={H - PAD.bottom} className="axis" />
        <line x1={PAD.left} x2={PAD.left} y1={PAD.top} y2={H - PAD.bottom} className="axis" />
        <text x={PAD.left - 6} y={PAD.top + 8} textAnchor="end" className="tick">
          {fmt(invert ? min : max)}
        </text>
        <text x={PAD.left - 6} y={H - PAD.bottom} textAnchor="end" className="tick">
          {fmt(invert ? max : min)}
        </text>
        <text x={PAD.left} y={H - 8} className="tick">
          {points[0].label}
        </text>
        <text x={W - PAD.right} y={H - 8} textAnchor="end" className="tick">
          {points[points.length - 1].label}
        </text>
        {segments
          .filter((s) => s.length > 1)
          .map((segment) => (
            <polyline key={segment[0]} points={segment.join(" ")} className="line" fill="none" />
          ))}
        {segments
          .filter((s) => s.length === 1)
          .map((segment) => {
            const [cx, cy] = segment[0].split(",");
            return <circle key={segment[0]} cx={cx} cy={cy} r="2.5" className="dot" />;
          })}
      </svg>
      <details>
        <summary>Show values as a table</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Date</th>
                <th scope="col">{unit}</th>
              </tr>
            </thead>
            <tbody>
              {points.map((p) => (
                <tr key={p.label}>
                  <td>{p.label}</td>
                  <td>{p.value === null ? "no data" : fmt(p.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
