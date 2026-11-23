import type { StatsOrigin } from "@repowise-dev/types/stats";
import { formatDateWith } from "../lib/format";

/**
 * The repo's opening line, as one sentence under the headline: the date, the
 * root commit's subject in mono, and who wrote it.
 *
 * Degrades a rung at a time: without a subject it is still a dated founding,
 * without a date it renders nothing.
 *
 * The date stays in UTC on purpose: it is a fact about the project, not the
 * reader, so two people looking at the same repo see the same founding day.
 */
export function OriginBlock({ data }: { data: StatsOrigin }) {
  if (!data.first_commit_at) return null;
  const author = data.first_commit_author;

  return (
    <p className="break-words text-xs leading-relaxed text-[var(--color-text-secondary)]">
      Root commit,{" "}
      {formatDateWith(data.first_commit_at, {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      })}
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
