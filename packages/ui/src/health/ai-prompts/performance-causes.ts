import type { PerformanceOpportunity } from "@repowise-dev/types/health";

import { biomarkerInfo } from "../biomarker-glossary";
import { pluralS } from "./shared";

/**
 * One performance cause as a prompt bullet. Shared by every prompt that hands
 * causes over (one file's, one area's), so a cause reads the same in each.
 * Titled by marker and location, as every other surface titles a cause; the
 * fix strategy is not the cause's name.
 */
export function performanceCauseLine(c: PerformanceOpportunity): string {
  const first = c.evidence[0];
  const symbol = c.intervention_symbol ?? first?.function_name ?? null;
  const at = symbol ? ` in \`${symbol}\`` : "";
  const line = first?.line_start != null ? ` (line ${first.line_start})` : "";
  const reach =
    c.affected_call_sites_total > 1
      ? ` Reaches ${c.affected_call_sites_total} call sites across ${c.affected_files_total} file${pluralS(
          c.affected_files_total,
        )}.`
      : "";
  // A summary row carries the plan reason but not the actionability reason.
  const why = c.actionability_reason || c.plan_reason;
  const fix = c.fix ? ` Recorded fix: ${c.fix.strategy} (${c.fix.safety}). ${c.fix.rationale}` : "";
  return `**${biomarkerInfo(c.biomarker_type).label}**${at}${line} — ${why}${reach}${fix}`;
}
