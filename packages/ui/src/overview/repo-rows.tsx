import * as React from "react";
import { bandForScore, HEALTH_BAND_LABEL } from "@repowise-dev/types/health";
import type { RepoIndexStatus } from "@repowise-dev/types/repos";
import { healthInk } from "../health/tokens";
import { formatNumber, formatRelativeTime } from "../lib/format";
import { RepoAvatar } from "./repo-avatar";

export interface RepoRow {
  id: string;
  name: string;
  localPath: string;
  href: string;
  status: RepoIndexStatus;
  /** Latest health score, 0-10. `null` means never analysed, which must not
   *  render as a 0 — that reads as "analysed, and terrible". */
  health: number | null;
  fileCount: number;
  hotspotCount: number;
  docPageCount: number;
  docFreshPageCount: number;
  deadExportCount: number;
  updatedAt: string | null;
  /** Index is behind the working tree. `null` when the comparison could not
   *  run, which is not the same as up to date, so neither gets a marker. */
  indexBehind: boolean | null;
}

export interface RepoRowsProps {
  repos: RepoRow[];
  LinkComponent?: React.ElementType | undefined;
  /**
   * Per-row secondary actions, rendered outside the row's link.
   *
   * The row's one verb is "open this repository" and it belongs to the whole
   * row; anything else the host wants goes here, where it can be an overflow
   * rather than a second competing target. Kept as a slot because deleting a
   * repo needs a confirm dialog and therefore a client boundary, which this
   * component does not have and should not grow.
   */
  actionsFor?: ((repo: RepoRow) => React.ReactNode) | undefined;
}

/** A marker that renders only when there is something to do. */
function StatusMark({ repo }: { repo: RepoRow }) {
  if (repo.status === "missing_dir") {
    return <Mark color="var(--color-error)" label="Directory missing" />;
  }
  if (repo.status !== "indexed") {
    return <Mark color="var(--color-warning)" label="Not indexed yet" />;
  }
  if (repo.indexBehind === true) {
    return <Mark color="var(--color-caution)" label="Index behind HEAD" />;
  }
  return null;
}

/**
 * Dot plus word, never a filled pill.
 *
 * A tinted ground, a border and coloured text on a token that repeats once per
 * row tiles into stripes down a list and outweighs the repo names it belongs
 * to. The word stays because these states are amber and amber-adjacent, which
 * is exactly the pair colour alone cannot separate.
 */
function Mark({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-medium" style={{ color }}>
      <span
        aria-hidden
        className="h-1.5 w-1.5 shrink-0 rounded-full"
        style={{ background: color }}
      />
      {label}
    </span>
  );
}

/** The figures line: every number carries the noun it counts. */
function figuresFor(repo: RepoRow): string[] {
  if (repo.status !== "indexed") return [];
  const parts = [`${formatNumber(repo.fileCount)} files`];
  if (repo.docPageCount > 0) {
    const pct = Math.round((repo.docFreshPageCount / repo.docPageCount) * 100);
    parts.push(`${pct}% of ${formatNumber(repo.docPageCount)} doc pages fresh`);
  }
  if (repo.hotspotCount > 0) parts.push(`${formatNumber(repo.hotspotCount)} hotspots`);
  if (repo.deadExportCount > 0) {
    parts.push(`${formatNumber(repo.deadExportCount)} unused exports`);
  }
  return parts;
}

/**
 * The repositories on this machine, as hairline rows.
 *
 * The multi-repo dashboard used to open with four cross-repo totals and put
 * the repos themselves in a card underneath. Summing findings across unrelated
 * repositories produces a number with no action attached to it — there is no
 * thing you do about "the total dead code across your side project and your
 * monorepo" — so the list is the subject here and the aggregates moved into
 * the ribbon above it.
 *
 * Health leads the right-hand column because it is the figure that decides
 * which repo you open. It is painted on `bandForScore`, the canonical three
 * bands, rather than the five-step `healthBand` reading: that one belongs to a
 * lede, where nothing adjacent contradicts it, and a column of rows is the
 * case where two ladders visibly disagree.
 */
export function RepoRows({ repos, LinkComponent, actionsFor }: RepoRowsProps) {
  const A = LinkComponent ?? "a";
  if (repos.length === 0) return null;

  return (
    <ul className="m-0 list-none divide-y divide-[var(--color-border-default)] border-t border-[var(--color-border-default)] p-0">
      {repos.map((repo) => {
        const figures = figuresFor(repo);
        const actions = actionsFor?.(repo);
        const band = repo.health === null ? null : bandForScore(repo.health);

        return (
          <li key={repo.id} className="group">
            {/* The link and the actions are siblings, not nested: a button
                inside an anchor is invalid and swallows its own clicks. */}
            <div className="flex items-start gap-3 py-3.5 transition-colors hover:bg-[var(--color-bg-wash-hover)] sm:gap-4">
              <A
                href={repo.href}
                className="flex min-w-0 flex-1 items-start gap-3 no-underline sm:gap-4"
              >
                <RepoAvatar name={repo.name} size={32} className="mt-0.5" />

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                    {/* No truncation. If a repo name needs an ellipsis the
                        layout is wrong, and a directory name is exactly the
                        string somebody is scanning for. */}
                    <span className="text-[15px] font-medium text-[var(--color-text-primary)] transition-colors group-hover:text-[var(--color-accent-primary)]">
                      {repo.name}
                    </span>
                    <StatusMark repo={repo} />
                  </div>

                  <p className="mt-0.5 font-mono text-[11px] text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
                    {repo.localPath}
                  </p>

                  <p className="mt-1.5 text-xs tabular-nums text-[var(--color-text-secondary)]">
                    {figures.length > 0 ? (
                      figures.join(" · ")
                    ) : (
                      <span className="text-[var(--color-text-tertiary)]">
                        Run an index to see this repository&rsquo;s figures.
                      </span>
                    )}
                    {repo.updatedAt && (
                      <span className="text-[var(--color-text-tertiary)]">
                        {figures.length > 0 ? " · " : " "}
                        Synced {formatRelativeTime(repo.updatedAt)}
                      </span>
                    )}
                  </p>
                </div>
              </A>

              <div className="flex shrink-0 items-start gap-2 sm:gap-4">
                <div className="w-[74px] text-right sm:w-[92px]">
                  <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                    Health
                  </p>
                  {repo.health === null || band === null ? (
                    <p
                      className="mt-1 text-[15px] leading-none text-[var(--color-text-tertiary)]"
                      title="No health snapshot yet — this repository has not been analysed."
                    >
                      &mdash;
                    </p>
                  ) : (
                    <>
                      <p
                        className="mt-1 text-[22px] font-semibold leading-none tabular-nums"
                        style={{ color: healthInk(repo.health) }}
                      >
                        {repo.health.toFixed(1)}
                      </p>
                      <p
                        className="mt-1 text-[11px]"
                        style={{ color: healthInk(repo.health) }}
                      >
                        {HEALTH_BAND_LABEL[band]}
                      </p>
                    </>
                  )}
                </div>
                {actions}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
