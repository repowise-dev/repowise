/**
 * Rendering scored biomarker findings into prompt text, shared by the
 * refactor prompt and the file-health prompt.
 */

import { biomarkerInfo, CATEGORY_LABEL } from "../biomarker-glossary";
import { bulletList, pluralS } from "./shared";

/** The fields of a scored finding that the prompts render. */
export interface PromptFinding {
  biomarker_type: string;
  severity: string;
  function_name: string | null;
  line_start?: number | null | undefined;
  line_end?: number | null | undefined;
  health_impact: number;
  reason: string;
  details?: Record<string, unknown> | null | undefined;
}

/** Highest health impact first, without reordering the caller's array. */
export function rankByImpact<T extends { health_impact: number }>(findings: T[]): T[] {
  return findings.slice().sort((a, b) => b.health_impact - a.health_impact);
}

/**
 * History findings as context, not work. They count toward the score but come
 * from the commit log, so no edit to the file can clear them.
 */
export function historyContextBlock(
  findings: { biomarker_type: string; reason: string; health_impact: number }[],
): string | null {
  if (findings.length === 0) return null;
  const ranked = rankByImpact(findings);
  const cost = ranked.reduce((sum, f) => sum + f.health_impact, 0);
  return [
    bulletList(ranked.map((f) => `**${biomarkerInfo(f.biomarker_type).label}** - ${f.reason}`)),
    "",
    `These are measured from this file's git history, not its code, and account for -${cost.toFixed(2)} points of its score. **Do not try to fix them.** No edit to this file will clear one; they move only as its commit history moves. Read them as background on how this code behaves over time, and let them raise your care where the structural work above touches the same regions.`,
  ].join("\n");
}

/** Typed reads over a finding's free-form `details` payload. */
interface DetailFields {
  num(key: string): number | null;
  str(key: string): string | null;
}

function detailFields(details: Record<string, unknown>): DetailFields {
  return {
    // The payload comes from JSON written by several detector versions, so a
    // count may arrive as a numeric string.
    num: (key) => {
      const v = details[key];
      if (typeof v === "number" && Number.isFinite(v)) return v;
      if (typeof v === "string" && v !== "" && Number.isFinite(Number(v))) {
        return Number(v);
      }
      return null;
    },
    str: (key) => {
      const v = details[key];
      return typeof v === "string" && v.length > 0 ? v : null;
    },
  };
}

function hiddenCouplingContext(d: DetailFields): string | null {
  const partner = d.str("partner");
  if (!partner) return null;
  const co = d.num("co_change_count");
  const corr = d.num("correlation");
  const pct = corr != null ? `${Math.round(corr * 100)}%` : null;
  const tail = [
    co != null ? `${co} co-changes` : null,
    pct ? `${pct} of shared commits` : null,
  ]
    .filter(Boolean)
    .join(" — ");
  return `Partner file: \`${partner}\`${tail ? ` — ${tail}` : ""}`;
}

function complexConditionalContext(d: DetailFields): string | null {
  const ops = d.num("operator_count");
  if (ops == null) return null;
  return `Boolean operators in this condition: ${ops}`;
}

function functionHotspotContext(d: DetailFields): string | null {
  // Older payloads spell these `mod_count` and `p80`.
  const mod = d.num("modification_count") ?? d.num("mod_count");
  const p80 = d.num("repo_p80") ?? d.num("p80");
  if (mod == null) return null;
  return `Function modified across ${mod} distinct commits${p80 != null ? ` (repo p80 = ${p80})` : ""}`;
}

function codeAgeVolatilityContext(d: DetailFields): string | null {
  const age = d.num("median_age_days");
  const recent = d.num("recent_mod_count");
  if (age == null && recent == null) return null;
  const parts: string[] = [];
  if (age != null) parts.push(`median line age ~${age} days`);
  if (recent != null) parts.push(`${recent} distinct commits in last 30 days`);
  return parts.join(", ");
}

/** Biomarkers whose details add to the reason line. A Map, so no type hits a prototype key. */
const EXTRA_CONTEXT = new Map<string, (d: DetailFields) => string | null>([
  ["hidden_coupling", hiddenCouplingContext],
  ["complex_conditional", complexConditionalContext],
  ["function_hotspot", functionHotspotContext],
  ["code_age_volatility", codeAgeVolatilityContext],
]);

export function biomarkerExtraContext(
  biomarkerType: string,
  details: Record<string, unknown> | null | undefined,
): string | null {
  if (!details) return null;
  const render = EXTRA_CONTEXT.get(biomarkerType);
  return render ? render(detailFields(details)) : null;
}

function findingLocation(f: PromptFinding): string {
  if (!f.function_name) return "file-level";
  const span = f.line_start
    ? ` (line ${f.line_start}${f.line_end ? `–${f.line_end}` : ""})`
    : "";
  return `function \`${f.function_name}\`${span}`;
}

/** One finding as a numbered entry: what it is, where, why, and what was seen. */
function findingEntry(f: PromptFinding, index: number, suggestion: string | undefined): string {
  const info = biomarkerInfo(f.biomarker_type);
  const extra = biomarkerExtraContext(f.biomarker_type, f.details);
  return [
    `${index + 1}. **${info.label}** · ${CATEGORY_LABEL[info.category]} · ${f.severity.toUpperCase()} · health impact −${f.health_impact.toFixed(2)}`,
    `   - Where: ${findingLocation(f)}`,
    `   - Why it's a problem: ${info.description}`,
    `   - Observed: ${f.reason}`,
    extra ? `   - Extra context: ${extra}` : null,
    suggestion ? `   - Suggested direction: ${suggestion}` : null,
  ]
    .filter(Boolean)
    .join("\n");
}

/** Numbered entries, with the host's suggested direction per marker when given. */
export function findingEntries(
  findings: PromptFinding[],
  suggestions?: Record<string, string>,
): string {
  return findings
    .map((f, i) => findingEntry(f, i, suggestions?.[f.biomarker_type]))
    .join("\n\n");
}

/** The findings past the detailed cap as one line grouped by marker. */
export function remainderRollup(remainder: PromptFinding[], followUp: string): string | null {
  if (remainder.length === 0) return null;
  const counts = new Map<string, number>();
  for (const f of remainder) {
    counts.set(f.biomarker_type, (counts.get(f.biomarker_type) ?? 0) + 1);
  }
  const grouped = Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([type, n]) => `${n}× ${biomarkerInfo(type).label}`)
    .join(", ");
  const total = remainder.reduce((s, f) => s + f.health_impact, 0);
  return `…and ${remainder.length} more lower-impact finding${pluralS(
    remainder.length,
  )} (${grouped}; −${total.toFixed(2)} total). ${followUp}`;
}
