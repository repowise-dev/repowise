import type * as React from "react";
import { PageLede } from "../shared/page-lede";
import { HealthDeltaMark, type HealthDeltaLabels } from "./health-delta-mark";
import { betterCount, healthDeltaKind, worseCount, type HealthDeltaCounts } from "./health-delta";

export type HealthDeltaLedeVariant = "unknown" | "stale" | "improved" | "neutral" | "worse";

export interface HealthDeltaLedeRead {
  variant: HealthDeltaLedeVariant;
  /** The figure: findings made worse, or resolved when nothing got worse;
   *  null when no current count can be shown. */
  value: number | null;
  better: number;
  worse: number;
}

/** Which reading a delta gets. A stale delta keeps its direction (the mark)
 *  but shows no figure: its counts do not compare with current ones. */
export function readHealthDeltaLede(delta: HealthDeltaCounts | null | undefined): HealthDeltaLedeRead {
  if (!delta) return { variant: "unknown", value: null, better: 0, worse: 0 };
  const better = betterCount(delta);
  const worse = worseCount(delta);
  if (delta.stale) return { variant: "stale", value: null, better, worse };
  const kind = healthDeltaKind(delta);
  if (kind === "improved") return { variant: "improved", value: better, better, worse };
  if (kind === "neutral") return { variant: "neutral", value: 0, better, worse };
  return { variant: "worse", value: worse, better, worse };
}

/** Every word the lede says; hosts pass translations, English by default. */
export interface HealthDeltaLedeCopy {
  label: string;
  unitWorse: (worse: number, better: number) => string;
  unitBetter: (better: number) => string;
  unitNeutral: string;
  worse: string;
  improved: (better: number) => string;
  neutral: string;
  stale: string;
  unknown: string;
}

const s = (n: number) => (n === 1 ? "" : "s");

const DEFAULT_COPY: HealthDeltaLedeCopy = {
  label: "What this did to code health",
  unitWorse: (worse, better) => `finding${s(worse)} introduced or worsened · ${better} resolved`,
  unitBetter: (better) => `finding${s(better)} resolved · nothing made worse`,
  unitNeutral: "findings changed",
  worse: "Each one is listed below by file, worst first, with the symbol and line.",
  improved: (better) => `Resolved ${better} finding${s(better)} and made nothing worse. Nice work.`,
  neutral: "Compared clean: nothing introduced, worsened or resolved.",
  stale: "An earlier analysis read the direction shown; its counts do not compare with the current analysis.",
  unknown: "Not analysed for code health yet.",
};

export interface HealthDeltaLedeProps {
  delta: HealthDeltaCounts | null | undefined;
  copy?: Partial<HealthDeltaLedeCopy> | undefined;
  /** Words for the mark beside the figure. */
  markLabels?: Partial<HealthDeltaLabels> | undefined;
  action?: React.ReactNode;
  /** Extra reading under the sentence, e.g. what was not compared. */
  children?: React.ReactNode;
}

/**
 * What a commit or a change did to code health, figure first: how many
 * findings it made worse (or, when it only helped, how many it resolved), the
 * dot-and-word mark at body size beside it, one reading and the primary
 * action. One implementation for the change page and the commit sheet.
 */
export function HealthDeltaLede({ delta, copy, markLabels, action, children }: HealthDeltaLedeProps) {
  const c = { ...DEFAULT_COPY, ...copy };
  const r = readHealthDeltaLede(delta);
  const unit =
    r.variant === "worse"
      ? c.unitWorse(r.worse, r.better)
      : r.variant === "improved"
        ? c.unitBetter(r.better)
        : r.variant === "neutral"
          ? c.unitNeutral
          : undefined;
  const reading = {
    worse: c.worse,
    improved: c.improved(r.better),
    neutral: c.neutral,
    stale: c.stale,
    unknown: c.unknown,
  }[r.variant];
  return (
    <PageLede
      label={c.label}
      value={r.value === null ? "—" : String(r.value)}
      unit={unit}
      badge={<HealthDeltaMark delta={delta ?? null} labels={markLabels ?? {}} figures={false} />}
      action={action}
    >
      <p>{reading}</p>
      {children}
    </PageLede>
  );
}
