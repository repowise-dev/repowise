/**
 * Rendering scored biomarker findings into prompt text, shared by the
 * refactor prompt and the file-health prompt.
 */

import { biomarkerInfo } from "../biomarker-glossary";
import { bulletList } from "./shared";

/**
 * History findings, stated as context rather than as work.
 *
 * They are scored, so leaving them out would not explain the file's number,
 * but they are measured from the commit log: an agent handed them in a fix
 * list will either edit the file until it gives up or invent a change that
 * cannot move them. They get their own section and an explicit instruction.
 */
export function historyContextBlock(
  findings: { biomarker_type: string; reason: string; health_impact: number }[],
): string | null {
  if (findings.length === 0) return null;
  const ranked = findings.slice().sort((a, b) => b.health_impact - a.health_impact);
  const cost = ranked.reduce((sum, f) => sum + f.health_impact, 0);
  return [
    bulletList(ranked.map((f) => `**${biomarkerInfo(f.biomarker_type).label}** - ${f.reason}`)),
    "",
    `These are measured from this file's git history, not its code, and account for -${cost.toFixed(2)} points of its score. **Do not try to fix them.** No edit to this file will clear one; they move only as its commit history moves. Read them as background on how this code behaves over time, and let them raise your care where the structural work above touches the same regions.`,
  ].join("\n");
}

export function biomarkerExtraContext(
  biomarkerType: string,
  details: Record<string, unknown> | null | undefined,
): string | null {
  if (!details) return null;
  const numField = (k: string): number | null => {
    const v = details[k];
    if (typeof v === "number" && Number.isFinite(v)) return v;
    if (typeof v === "string" && v !== "" && Number.isFinite(Number(v))) {
      return Number(v);
    }
    return null;
  };
  const strField = (k: string): string | null => {
    const v = details[k];
    return typeof v === "string" && v.length > 0 ? v : null;
  };

  if (biomarkerType === "hidden_coupling") {
    const partner = strField("partner");
    if (!partner) return null;
    const co = numField("co_change_count");
    const corr = numField("correlation");
    const pct = corr != null ? `${Math.round(corr * 100)}%` : null;
    const tail = [
      co != null ? `${co} co-changes` : null,
      pct ? `${pct} of shared commits` : null,
    ]
      .filter(Boolean)
      .join(" — ");
    return `Partner file: \`${partner}\`${tail ? ` — ${tail}` : ""}`;
  }
  if (biomarkerType === "complex_conditional") {
    const ops = numField("operator_count");
    if (ops == null) return null;
    return `Boolean operators in this condition: ${ops}`;
  }
  if (biomarkerType === "function_hotspot") {
    const mod = numField("modification_count") ?? numField("mod_count");
    const p80 = numField("repo_p80") ?? numField("p80");
    if (mod == null) return null;
    return `Function modified across ${mod} distinct commits${p80 != null ? ` (repo p80 = ${p80})` : ""}`;
  }
  if (biomarkerType === "code_age_volatility") {
    const age = numField("median_age_days");
    const recent = numField("recent_mod_count");
    if (age == null && recent == null) return null;
    const parts: string[] = [];
    if (age != null) parts.push(`median line age ~${age} days`);
    if (recent != null) parts.push(`${recent} distinct commits in last 30 days`);
    return parts.join(", ");
  }
  return null;
}
