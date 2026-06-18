import * as React from "react";
import { MetricCard } from "../shared/metric-card";

export interface KpiItem {
  label: string;
  value: string;
  href: string;
  /**
   * `positive` is a *good-vs-bad* flag (green/up vs red/down), NOT up-vs-down:
   * a metric can grow while still being neutral or bad. Set `neutral` to render
   * the delta uncolored for metrics where a change carries no value judgement
   * (e.g. file-count growth).
   */
  delta?: { value: string; positive: boolean; neutral?: boolean };
  /** 0–100 fill for a mini gauge bar beneath the value (e.g. coverage %). */
  gauge?: number;
}

/**
 * Builds a signed delta descriptor from a raw numeric change.
 *
 * `neutral` renders the delta uncolored — use it when a positive change is not
 * inherently "good" (the color semantics are good-vs-bad, not up-vs-down).
 */
export function kpiDelta(
  delta: number | null | undefined,
  neutral = false,
): KpiItem["delta"] {
  if (delta == null || delta === 0) return undefined;
  return {
    value: `${delta > 0 ? "+" : ""}${delta}`,
    positive: delta > 0,
    ...(neutral ? { neutral: true } : {}),
  };
}

function Gauge({ value }: { value: number }) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
      <div
        className="h-full rounded-full bg-[var(--color-accent-fill)]"
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}

export interface KpiStripProps {
  items: KpiItem[];
  /** Injected link component (e.g. Next's Link); defaults to a plain anchor. */
  LinkComponent?: React.ElementType<{
    href: string;
    className?: string;
    children: React.ReactNode;
  }>;
  className?: string;
}

/**
 * Airy KPI bar — gap-separated stat tiles (no divider chrome), each rendered
 * with the canonical `MetricCard`. Percentage metrics carry a mini gauge.
 * Fully presentational: data and the link component arrive via props.
 */
export function KpiStrip({ items, LinkComponent, className }: KpiStripProps) {
  return (
    <div
      className={`grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5 ${className ?? ""}`}
    >
      {items.map((kpi) => (
        <MetricCard
          key={kpi.label}
          label={kpi.label}
          value={kpi.value}
          href={kpi.href}
          {...(kpi.delta ? { delta: kpi.delta } : {})}
          {...(LinkComponent ? { LinkComponent } : {})}
          {...(typeof kpi.gauge === "number"
            ? { distBar: <Gauge value={kpi.gauge} /> }
            : {})}
        />
      ))}
    </div>
  );
}
