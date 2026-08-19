import * as React from "react";
import { LedeLink, PageLede } from "../shared/page-lede";

export interface HealthLedeProps {
  /** Defect-risk headline, 1–10. Null until the first health run. */
  score: number | null;
  maintainability?: number | null;
  /** Static performance risk, 1–10. */
  performance?: number | null;
  /** Health of the highest-churn files. The interesting number, usually. */
  hotspotHealth?: number | null;
  hotspotCount?: number;
  fileCount?: number;
  /** "Full health report →" target. */
  href: string;
  LinkComponent?: React.ElementType;
}

/** 1–10 bands. Shared with HealthOverviewCard's thresholds on purpose: two
 *  surfaces disagreeing about what "Good" means is worse than duplication. */
export function healthBand(v: number): { color: string; label: string } {
  if (v >= 8) return { color: "var(--color-success)", label: "Excellent" };
  if (v >= 6.5) return { color: "var(--color-success)", label: "Good" };
  if (v >= 5) return { color: "var(--color-caution)", label: "Fair" };
  if (v >= 3.5) return { color: "var(--color-warning)", label: "Needs work" };
  return { color: "var(--color-error)", label: "Critical" };
}

/**
 * Code health as a headline number and a plain sentence, not a card of tiles.
 *
 * The figure alone is not readable: "329 risks · 9.9/10" looks like a
 * contradiction until something says the score is a bounded summary of the
 * findings rather than a count of them. So the prose is load-bearing, not
 * decoration — it is the part that makes the number mean anything, and it is
 * why the public repo landing page reads calm while showing the same data.
 *
 * Health keeps the largest number on the page because it is the product's
 * moat. It does not get the whole top of the page: the column beside it
 * carries the other reasons people open Overview.
 */
export function HealthLede({
  score,
  maintainability,
  performance,
  hotspotHealth,
  hotspotCount = 0,
  fileCount = 0,
  href,
  LinkComponent,
}: HealthLedeProps) {
  if (score == null) {
    return (
      <div className="flex flex-col gap-2">
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          Code health
        </p>
        <p className="max-w-[54ch] text-sm text-[var(--color-text-secondary)]">
          Health scores land with the first index: complexity, duplication, coverage,
          churn and ownership across every file.
        </p>
      </div>
    );
  }

  const band = healthBand(score);
  const hot = hotspotHealth != null ? healthBand(hotspotHealth) : null;

  // Assembled rather than interpolated inline: a repo can have measured one
  // pillar and not the other, and the naive version produced "Maintainability
  // scores 8.6. The three are scored separately" (there were two) or a sentence
  // starting lowercase when only performance was present.
  const pillars: string[] = [];
  if (maintainability != null) pillars.push(`maintainability ${maintainability.toFixed(1)}`);
  if (performance != null) pillars.push(`static performance risk ${performance.toFixed(1)}`);
  const pillarSentence =
    pillars.length === 0
      ? null
      : `It also scores ${pillars.join(" and ")} out of 10. ${
          pillars.length === 1
            ? "The two are scored separately and never blended into one number."
            : "The three are scored separately and never blended into one number."
        }`;

  return (
    <PageLede
      label="Code health"
      value={score.toFixed(1)}
      valueColor={band.color}
      unit="out of 10"
      band={band}
      action={
        <LedeLink href={href} LinkComponent={LinkComponent}>
          Full health report
        </LedeLink>
      }
    >
      <p>
        This codebase scores{" "}
        <strong className="font-semibold text-[var(--color-text-primary)]">
          {score.toFixed(1)} out of 10
        </strong>{" "}
        on defect risk, which we rate {band.label.toLowerCase()}.
        {pillarSentence && ` ${pillarSentence}`}
        {hotspotCount > 0 && hot && (
          <>
            {" "}
            The files you change most are the weak spot:{" "}
            <strong className="font-semibold text-[var(--color-text-primary)]">
              {hotspotCount.toLocaleString()}
              {fileCount > 0 ? ` of ${fileCount.toLocaleString()}` : ""} files
            </strong>{" "}
            are git hotspots, and they average{" "}
            <strong className="font-semibold" style={{ color: hot.color }}>
              {hotspotHealth!.toFixed(1)}
            </strong>
            .
          </>
        )}
      </p>
    </PageLede>
  );
}
