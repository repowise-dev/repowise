import type { StatsOrigin } from "@repowise-dev/types/stats";
import { formatDate } from "../lib/format";

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
      Root commit, {formatDate(data.first_commit_at)}
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
