export interface SparklineProps {
  values: number[];
  width?: number;
  height?: number;
  stroke?: string;
  fill?: string;
  /** Optional fixed Y-axis range. Defaults to min/max of values. */
  domain?: [number, number];
}

export function Sparkline({
  values,
  width = 80,
  height = 24,
  stroke = "currentColor",
  fill = "none",
  domain,
}: SparklineProps) {
  if (!values || values.length === 0) {
    return (
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden>
        <line
          x1={0}
          x2={width}
          y1={height / 2}
          y2={height / 2}
          stroke="currentColor"
          strokeOpacity={0.15}
          strokeDasharray="2 2"
        />
      </svg>
    );
  }
  if (values.length === 1) {
    return (
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden>
        <circle cx={width / 2} cy={height / 2} r={2} fill={stroke} />
      </svg>
    );
  }
  const [domMin, domMax] = domain ?? [Math.min(...values), Math.max(...values)];
  const range = domMax - domMin || 1;
  const stepX = width / (values.length - 1);
  const pts = values.map((v, i) => {
    const x = i * stepX;
    const y = height - ((v - domMin) / range) * height;
    return [x, y] as const;
  });
  const d = pts.map(([x, y], i) => (i === 0 ? `M${x},${y}` : `L${x},${y}`)).join(" ");
  const area = `${d} L${width},${height} L0,${height} Z`;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden>
      {fill !== "none" ? <path d={area} fill={fill} /> : null}
      <path d={d} stroke={stroke} strokeWidth={1.5} fill="none" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={pts[pts.length - 1]![0]} cy={pts[pts.length - 1]![1]} r={1.8} fill={stroke} />
    </svg>
  );
}
