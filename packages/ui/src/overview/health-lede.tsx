import * as React from "react";
import { healthBand } from "../health/tokens";
import { LedeLink, PageLede } from "../shared/page-lede";

export interface HealthLedeProps {
  /** Code-health headline, 1–10. Higher is better. Null before the first run. */
  score: number | null;
  maintainability?: number | null | undefined;
  /** Static performance score, 1–10. Higher is better, like the other two. */
  performance?: number | null | undefined;
  /** Health of the highest-churn files. The interesting number, usually. */
  hotspotHealth?: number | null | undefined;
  hotspotCount?: number | undefined;
  fileCount?: number | undefined;
  /** "Full health report →" target. */
  href: string;
  LinkComponent?: React.ElementType | undefined;
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
  // "static performance risk 9.7" read as an alarm and meant the opposite: the
  // figure is a score on the same 1-10 ladder as the other two, where 9.7 is
  // close to clean. The noun has to agree with the direction of the number, so
  // it is named like its siblings and the word "risk" is left to the findings
  // count on the health page, where more genuinely is worse.
  const pillars: string[] = [];
  if (maintainability != null) pillars.push(`maintainability ${maintainability.toFixed(1)}`);
  if (performance != null) pillars.push(`static performance ${performance.toFixed(1)}`);
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
        for code health, which we rate {band.label.toLowerCase()}.
        {pillarSentence && ` ${pillarSentence}`}
        {/* This used to open "The files you change most are the weak spot",
            which reads as an indictment of something every codebase does. Hot
            files scoring below the average is the premise the whole product
            rests on, not a failure: it is why ranking by git history finds
            defects that reading the code alone does not. Same two figures,
            stated as the finding they are, and pointed at the files rather
            than at the reader. */}
        {hotspotCount > 0 && hot && (
          <>
            {" "}
            Risk is concentrated, as it usually is:{" "}
            <strong className="font-semibold text-[var(--color-text-primary)]">
              {hotspotCount.toLocaleString()}
              {fileCount > 0 ? ` of ${fileCount.toLocaleString()}` : ""} files
            </strong>{" "}
            are git hotspots, and they average{" "}
            <strong className="font-semibold" style={{ color: hot.color }}>
              {hotspotHealth!.toFixed(1)}
            </strong>
            {" "}— which is where the fixes pay off most.
          </>
        )}
      </p>
    </PageLede>
  );
}
