import type { StatsOrigin } from "@repowise-dev/types/stats";
import { parseDate } from "../lib/format";

/** Absolute date in UTC.
 *
 *  Deliberately not the browser's timezone: this is the repo's founding
 *  moment, a fact about the project rather than about the reader, and
 *  rendering it locally would show two birthdays to two people looking at the
 *  same repo. */
export function utcDate(iso: string): string {
  return parseDate(iso).toLocaleDateString("en-US", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/**
 * The repo's opening line, as one sentence under the headline: the date, the
 * root commit's subject in mono, and who wrote it.
 *
 * Degrades a rung at a time: without a subject it is still a dated founding,
 * without a date it renders nothing.
 */
export function OriginBlock({ data }: { data: StatsOrigin }) {
  if (!data.first_commit_at) return null;
  const author = data.first_commit_author;

  return (
    <p className="break-words text-xs leading-relaxed text-[var(--color-text-secondary)]">
      Root commit, {utcDate(data.first_commit_at)}
      {data.first_commit_subject && (
        <>
          :{" "}
          <q className="font-mono text-[var(--color-text-primary)]">{data.first_commit_subject}</q>
        </>
      )}
      {author && <> by {author}</>}.
    </p>
  );
}
