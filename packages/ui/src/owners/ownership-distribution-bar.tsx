import * as React from "react";
import type { OwnerListEntry } from "@repowise-dev/types/owners";
import { RAMP_TAIL, rampStep } from "../lib/ramp";

interface OwnershipDistributionBarProps {
  owners: OwnerListEntry[];
  /** Repo-wide contributor count (may exceed the loaded `owners`). */
  totalContributors: number;
  onSelect?: (owner: OwnerListEntry) => void;
  /** Router link for the drill-in; defaults to a plain anchor. */
  hrefFor?: (owner: OwnerListEntry) => string;
  LinkComponent?: React.ElementType | undefined;
}

// Five ramp steps, then the tail. The old version drew ten segments in ten
// unrelated hues, which spent the page's entire colour budget on a
// distinction that carries no information: two contributors being rose and
// teal tells you nothing about either. Position in the ramp means share, so
// the bar now reads left to right as "most owned to least".
const TOP_N = 5;

interface Segment {
  owner: OwnerListEntry | null;
  label: string;
  files: number;
  pct: number;
  fill: string;
}

/**
 * How owned files spread across contributors, as one proportional bar.
 *
 * The point is the shape: a bar dominated by one or two segments is a
 * concentration risk, an even spread is healthy. Segments and legend entries
 * drill into the owner profile.
 *
 * A server component. It had no state, and the previous `onSelect`-only API
 * forced every parent to be a client component just to route a click. It now
 * takes `hrefFor` as well, so a server-rendered page links and only a page
 * that genuinely needs the callback pays for hydration.
 */
export function OwnershipDistributionBar({
  owners,
  totalContributors,
  onSelect,
  hrefFor,
  LinkComponent,
}: OwnershipDistributionBarProps) {
  const sorted = [...owners].sort((a, b) => b.files_owned - a.files_owned);
  const totalFiles = sorted.reduce((s, o) => s + o.files_owned, 0);
  if (sorted.length === 0 || totalFiles === 0) return null;

  const top = sorted.slice(0, TOP_N);
  const restFiles = totalFiles - top.reduce((s, o) => s + o.files_owned, 0);
  const restCount = Math.max(0, totalContributors - top.length);

  const segments: Segment[] = top.map((o, i) => ({
    owner: o,
    label: o.name || o.email || "unknown",
    files: o.files_owned,
    pct: (o.files_owned / totalFiles) * 100,
    fill: rampStep(i),
  }));
  if (restFiles > 0 && restCount > 0) {
    segments.push({
      owner: null,
      label: `${restCount.toLocaleString()} others`,
      files: restFiles,
      pct: (restFiles / totalFiles) * 100,
      fill: RAMP_TAIL,
    });
  }

  const A = LinkComponent ?? "a";

  return (
    <div className="flex flex-col gap-3">
      <div className="flex h-2.5 w-full overflow-hidden rounded-[3px]">
        {segments.map((s) => (
          <span
            key={s.owner?.key ?? "__rest"}
            className="h-full min-w-[3px] border-r border-[var(--color-bg-root)] last:border-r-0"
            style={{ width: `${s.pct}%`, background: s.fill }}
            title={`${s.label}: ${s.files.toLocaleString()} files (${s.pct.toFixed(1)}%)`}
          />
        ))}
      </div>

      {/* Legend. Names wrap rather than truncate — the old one capped each
          entry at 140px, so a long name was reported to the reader as a
          shorter name. */}
      <div className="flex flex-wrap gap-x-5 gap-y-1.5 text-xs">
        {segments.map((s) => {
          const body = (
            <>
              <span
                aria-hidden
                className="mt-[5px] h-2 w-2 shrink-0 rounded-[2px]"
                style={{ background: s.fill }}
              />
              <span className="text-[var(--color-text-secondary)] [text-wrap:pretty]">
                {s.label}
              </span>
              <span className="shrink-0 font-mono tabular-nums text-[var(--color-text-tertiary)]">
                {Math.round(s.pct)}%
              </span>
            </>
          );
          const cls = "flex items-start gap-1.5 text-left";
          if (!s.owner) {
            return (
              <span key="__rest" className={cls}>
                {body}
              </span>
            );
          }
          if (hrefFor) {
            return (
              <A
                key={s.owner.key}
                href={hrefFor(s.owner)}
                className={`${cls} no-underline hover:text-[var(--color-text-primary)]`}
              >
                {body}
              </A>
            );
          }
          if (onSelect) {
            const owner = s.owner;
            return (
              <button
                key={owner.key}
                type="button"
                onClick={() => onSelect(owner)}
                className={`${cls} hover:text-[var(--color-text-primary)]`}
              >
                {body}
              </button>
            );
          }
          return (
            <span key={s.owner.key} className={cls}>
              {body}
            </span>
          );
        })}
      </div>
    </div>
  );
}
