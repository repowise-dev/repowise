import * as React from "react";
import { healthBand } from "../health/tokens";
import { formatScore } from "@repowise-dev/types/health";
import { LedeLink, PageLede } from "../shared/page-lede";

export interface HealthLedeCopy {
  label: string;
  unit: string;
  reportLink: string;
  /** Shown before the first health run. */
  empty: string;
  /** Methodology behind the info tip beside the label. `counts` is null when
   *  the index has no hotspots. */
  hint: (counts: { hotspots: string; files: string } | null) => string;
  /** The one reading under the figure. `figure` is the coloured hotspot score;
   *  `holdsUp` is true when hotspots score at or above the whole codebase. */
  hotspotReading: (figure: React.ReactNode, holdsUp: boolean) => React.ReactNode;
}

export const HEALTH_LEDE_COPY: HealthLedeCopy = {
  label: "Code health",
  unit: "out of 10",
  reportLink: "Full health report",
  empty:
    "Health scores land with the first index: complexity, duplication, coverage, churn and ownership across every file.",
  hint: (counts) =>
    `Average code health of every scored file, from 1 to 10.${
      counts
        ? ` Hotspots are the ${counts.hotspots} of ${counts.files} files that git history changes and fixes most.`
        : ""
    }`,
  hotspotReading: (figure, holdsUp) =>
    holdsUp ? (
      <>Hotspots average {figure}, as healthy as the rest of the code.</>
    ) : (
      <>Hotspots average {figure}, the place fixes pay off most.</>
    ),
};

export interface HealthLedeProps {
  /** Code-health headline, 1–10. Higher is better. Null before the first run. */
  score: number | null;
  /** @deprecated Not shown on Overview; Code Health owns it. */
  maintainability?: number | null | undefined;
  /** @deprecated Not shown on Overview; Code Health owns it. */
  performance?: number | null | undefined;
  /** Health of the highest-churn files. */
  hotspotHealth?: number | null | undefined;
  hotspotCount?: number | undefined;
  fileCount?: number | undefined;
  /** "Full health report →" target. */
  href: string;
  LinkComponent?: React.ElementType | undefined;
  copy?: Partial<HealthLedeCopy> | undefined;
}

/**
 * Code health as one figure, its band, and one reading that points at where
 * work pays off. How the score is built sits behind the info tip.
 */
export function HealthLede({
  score,
  hotspotHealth,
  hotspotCount = 0,
  fileCount = 0,
  href,
  LinkComponent,
  copy,
}: HealthLedeProps) {
  const c = { ...HEALTH_LEDE_COPY, ...copy };
  if (score == null) {
    return (
      <div className="flex flex-col gap-2">
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          {c.label}
        </p>
        <p className="max-w-[54ch] text-sm text-[var(--color-text-secondary)]">{c.empty}</p>
      </div>
    );
  }

  const band = healthBand(score);
  const hot = hotspotCount > 0 && hotspotHealth != null ? hotspotHealth : null;

  return (
    <PageLede
      label={c.label}
      labelHint={c.hint(
        hot != null && fileCount > 0
          ? { hotspots: hotspotCount.toLocaleString(), files: fileCount.toLocaleString() }
          : null,
      )}
      value={formatScore(score)}
      valueColor={band.color}
      unit={c.unit}
      band={band}
      action={
        <LedeLink href={href} LinkComponent={LinkComponent}>
          {c.reportLink}
        </LedeLink>
      }
    >
      {hot != null && (
        <p>
          {c.hotspotReading(
            <strong className="font-semibold tabular-nums" style={{ color: healthBand(hot).color }}>
              {formatScore(hot)}
            </strong>,
            hot >= score,
          )}
        </p>
      )}
    </PageLede>
  );
}
