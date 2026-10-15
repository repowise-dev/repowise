"use client";

/**
 * Four or five facts with a dot and a word, so healthy against unhealthy reads
 * at a glance. Thresholds are the conventional ones (complexity 10 and 20, the
 * usual McCabe bands); an unmeasured fact says so in a neutral mark rather
 * than borrowing a band.
 */

import { useMemo } from "react";
import type { FileSignals } from "@repowise-dev/types/health";
import type { HealthDrawerMetric } from "../health-file-drawer";

type Tone = "good" | "warn" | "bad" | "unknown";
type Vital = { label: string; value: string; tone: Tone };

const VITAL_DOT: Record<Tone, string> = {
  good: "bg-[var(--color-success)]",
  warn: "bg-[var(--color-caution)]",
  bad: "bg-[var(--color-error)]",
  unknown: "bg-[var(--color-text-tertiary)]",
};

type Sign = (m: HealthDrawerMetric, s: FileSignals | null | undefined) => Vital | null;

/** Pick the tone for a value against two ascending limits. */
function band(value: number, warnAbove: number, badAbove: number): Tone {
  if (value > badAbove) return "bad";
  return value > warnAbove ? "warn" : "good";
}

const complexity: Sign = (m) => {
  if (m.max_ccn == null) return null;
  const tone = band(m.max_ccn, 10, 20);
  const value = tone === "bad" ? "high" : tone === "warn" ? "moderate" : "low";
  return { label: "Complexity", value, tone };
};

const testFile: Sign = (m) => ({
  label: "Test file",
  value: m.has_test_file ? "found" : "not found",
  tone: m.has_test_file ? "good" : "warn",
});

const coverage: Sign = (m) => {
  const pct = m.line_coverage_pct;
  if (pct == null) return { label: "Coverage", value: "not measured", tone: "unknown" };
  const tone: Tone = pct >= 80 ? "good" : pct >= 50 ? "warn" : "bad";
  return { label: "Coverage", value: `${pct.toFixed(0)}%`, tone };
};

const churn: Sign = (_m, s) => {
  if (s?.commit_count_90d == null) return null;
  const often = s.commit_count_90d >= 10;
  return { label: "Changes often", value: often ? "yes" : "no", tone: often ? "warn" : "good" };
};

const bugFixes: Sign = (_m, s) => {
  const n = s?.prior_defect_count;
  if (n == null) return null;
  if (n === 0) return { label: "Bug fixes", value: "none", tone: "good" };
  return n >= 3
    ? { label: "Bug fixes", value: "frequent", tone: "bad" }
    : { label: "Bug fixes", value: "a few", tone: "warn" };
};

/** Code facts only where health measures the code; an unanalysed file keeps
 *  its history facts and drops the rest, rather than "Test file: not found"
 *  on a Makefile. */
const CODE_SIGNS: Sign[] = [complexity, testFile, coverage];
const HISTORY_SIGNS: Sign[] = [churn, bugFixes];

function vitalSigns(m: HealthDrawerMetric, s: FileSignals | null | undefined): Vital[] {
  const signs = m.score != null ? [...CODE_SIGNS, ...HISTORY_SIGNS] : HISTORY_SIGNS;
  return signs.map((sign) => sign(m, s)).filter((v): v is Vital => v !== null);
}

export function VitalSigns({
  metric,
  signals,
}: {
  metric: HealthDrawerMetric;
  signals: FileSignals | null | undefined;
}) {
  const vitals = useMemo(() => vitalSigns(metric, signals), [metric, signals]);
  if (vitals.length === 0) return null;
  return (
    <ul
      aria-label="Vital signs"
      className="flex flex-wrap gap-x-5 gap-y-2 border-t border-[var(--color-border-default)] pt-3"
    >
      {vitals.map((v) => (
        <li key={v.label} className="flex items-center gap-1.5 text-xs">
          <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${VITAL_DOT[v.tone]}`} />
          <span className="text-[var(--color-text-tertiary)]">{v.label}</span>
          <span className="font-medium text-[var(--color-text-primary)]">{v.value}</span>
        </li>
      ))}
    </ul>
  );
}
