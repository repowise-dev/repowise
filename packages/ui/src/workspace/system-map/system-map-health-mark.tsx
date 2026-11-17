/**
 * A service's health in a row: the canonical dot, figure and band word
 * (`healthMark`), or "No score". Shared by the group panel and the services
 * table so both read the same.
 */

import { healthMark } from "../../health/tokens";

export function SystemMapHealthMark({ score, noScore = "No score" }: { score: number | null; noScore?: string }) {
  if (score == null) return <span className="shrink-0 text-xs text-[var(--color-text-secondary)]">{noScore}</span>;
  const mark = healthMark(score);
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5 text-xs">
      <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: mark.color }} />
      <span className="tabular-nums text-[var(--color-text-primary)]">{mark.value}</span>
      <span className="text-[var(--color-text-secondary)]">{mark.label}</span>
    </span>
  );
}
