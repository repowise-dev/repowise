import type { StatsOrigin } from "@repowise-dev/types/stats";
import { formatAgeDays, formatNumber, parseDate } from "../lib/format";

/** Absolute date in UTC.
 *
 *  Deliberately not the browser's locale timezone: this is the repo's founding
 *  moment, a fact about the project rather than about the reader, and rendering
 *  it locally would show two different birthdays to two people looking at the
 *  same repo (and slip a day for anyone far enough east or west). */
function foundingDate(iso: string): string {
  return parseDate(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/**
 * The repo's opening line, set as a commit rather than a statistic.
 *
 * Every other date on this page is a measurement; this one is the project's
 * origin story, so it gets the page's only quotation treatment — an amber rule,
 * the subject in mono at display size, and the prose underneath doing the work
 * that a "Project age: 1,232 days" tile never could.
 *
 * Degrades a rung at a time: without a subject it is still a dated founding,
 * without a date it renders nothing.
 */
export function OriginBlock({ data }: { data: StatsOrigin }) {
  if (!data.first_commit_at) return null;

  const author = data.first_commit_author;
  const age = data.age_days != null ? formatAgeDays(data.age_days) : null;
  const others = Math.max(0, data.contributor_count - 1);

  return (
    <section
      aria-label="Project origin"
      className="border-l-2 border-[var(--color-accent-primary)] pl-5"
    >
      <p className="font-mono text-[11px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        Root commit · {foundingDate(data.first_commit_at)}
      </p>

      {data.first_commit_subject && (
        <p className="mt-2 break-words font-mono text-[15px] font-medium leading-snug text-[var(--color-text-primary)] sm:text-lg">
          {data.first_commit_subject}
        </p>
      )}

      <p className="mt-2.5 text-sm text-[var(--color-text-secondary)]">
        {age ? (
          <>
            It started{" "}
            <strong className="font-semibold text-[var(--color-text-primary)]">{age} ago</strong>
          </>
        ) : (
          <>It started here</>
        )}
        {author && (
          <>
            , written by{" "}
            <strong className="font-semibold text-[var(--color-text-primary)]">{author}</strong>
          </>
        )}
        {". "}
        {others > 0 && (
          <span className="text-[var(--color-text-tertiary)]">
            Since then, {formatNumber(others)} more {others === 1 ? "person has" : "people have"}{" "}
            shown up.
          </span>
        )}
      </p>
    </section>
  );
}
