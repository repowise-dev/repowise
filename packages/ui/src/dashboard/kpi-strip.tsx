import * as React from "react";
import { MetricCard } from "../shared/metric-card";

export interface KpiItem {
  label: string;
  value: string;
  href: string;
  delta?: { value: string; positive: boolean };
  /** 0–100 fill for a mini gauge bar beneath the value (e.g. coverage %). */
  gauge?: number;
}

/** Builds a signed delta descriptor from a raw numeric change. */
export function kpiDelta(delta: number | null | undefined): KpiItem["delta"] {
  if (delta == null || delta === 0) return undefined;
  return { value: `${delta > 0 ? "+" : ""}${delta}`, positive: delta > 0 };
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
