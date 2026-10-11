import { ProportionBar } from "../shared/proportion-bar";
import { SEVERITY_LABEL, SEVERITY_SEGMENT, type Severity } from "./tokens";

export interface SeverityBreakdown {
  critical: number;
  high: number;
  medium: number;
  low: number;
}

export interface SeverityDistributionProps {
  breakdown: SeverityBreakdown;
  /** When true, also renders the per-severity counts below the bar. */
  showCounts?: boolean;
  height?: "sm" | "md";
}

const ORDER: Severity[] = ["critical", "high", "medium", "low"];

export function SeverityDistribution({
  breakdown,
  showCounts = true,
  height = "sm",
}: SeverityDistributionProps) {
  const total =
    breakdown.critical + breakdown.high + breakdown.medium + breakdown.low;
  if (total === 0) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">No findings.</p>
    );
  }
  return (
    <ProportionBar
      label="Findings by severity"
      size={height}
      sort={false}
      legend={showCounts}
      segments={ORDER.map((sev) => ({
        key: sev,
        label: SEVERITY_LABEL[sev],
        value: breakdown[sev],
        detail: breakdown[sev].toLocaleString(),
        color: SEVERITY_SEGMENT[sev],
      }))}
    />
  );
}
