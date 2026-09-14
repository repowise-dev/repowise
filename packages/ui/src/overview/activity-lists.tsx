import * as React from "react";
import { formatDateTime, formatRelativeTime, stripMarkdown } from "../lib/format";

export interface CommitRow {
  sha: string;
  short_sha: string;
  subject: string;
  author_name: string;
  committed_at: string | null;
}

export interface DecisionRow {
  id: string;
  title: string;
  status: string;
  created_at: string | null;
}

const STATUS_COLOR: Record<string, string> = {
  active: "var(--color-success)",
  proposed: "var(--color-info)",
  deprecated: "var(--color-error)",
  superseded: "var(--color-text-tertiary)",
};

/**
 * Recent commits as hairline rows rather than a card.
 *
 * Same content the card version showed; the difference is that it sits in the
 * page's own rhythm instead of introducing a box, and the subject line gets
 * the full column width so it stops truncating at a card's inner padding.
 */
export function CommitRows({
  commits,
  hrefFor,
  LinkComponent,
}: {
  commits: CommitRow[];
  hrefFor: (sha: string) => string;
  LinkComponent?: React.ElementType;
}) {
  const A = LinkComponent ?? "a";
  if (commits.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">No commits indexed yet.</p>
    );
  }

  return (
    <ul className="m-0 list-none divide-y divide-[var(--color-border-default)] border-t border-[var(--color-border-default)] p-0">
      {commits.map((c) => (
        <li key={c.sha}>
          <A
            href={hrefFor(c.sha)}
            className="group flex items-baseline gap-2.5 py-2 text-xs no-underline"
          >
            <span className="shrink-0 font-mono text-[10px] text-[var(--color-accent-primary)]">
              {c.short_sha}
            </span>
            <span className="min-w-0 flex-1 truncate text-[var(--color-text-secondary)] group-hover:text-[var(--color-text-primary)]">
              {c.subject}
            </span>
            {c.committed_at && (
              <span
                className="shrink-0 font-mono text-[10px] text-[var(--color-text-tertiary)]"
                title={`${c.author_name} · ${formatDateTime(c.committed_at)}`}
              >
                {formatRelativeTime(c.committed_at)}
              </span>
            )}
          </A>
        </li>
      ))}
    </ul>
  );
}

/** Recent decisions as hairline rows, with the status carried by a dot and a
 *  word rather than a badge — badges at this size are chrome, not signal. */
export function DecisionRows({
  decisions,
  hrefFor,
  LinkComponent,
}: {
  decisions: DecisionRow[];
  hrefFor: (id: string) => string;
  LinkComponent?: React.ElementType;
}) {
  const A = LinkComponent ?? "a";
  if (decisions.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">
        No decisions extracted yet.
      </p>
    );
  }

  return (
    <ul className="m-0 list-none divide-y divide-[var(--color-border-default)] border-t border-[var(--color-border-default)] p-0">
      {decisions.map((d) => (
        <li key={d.id}>
          <A
            href={hrefFor(d.id)}
            className="group flex items-baseline gap-2.5 py-2 text-xs no-underline"
          >
            <span
              aria-hidden
              className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full"
              style={{ background: STATUS_COLOR[d.status] ?? "var(--color-text-tertiary)" }}
            />
            <span className="min-w-0 flex-1 truncate text-[var(--color-text-secondary)] group-hover:text-[var(--color-text-primary)]">
              {stripMarkdown(d.title)}
            </span>
            <span className="shrink-0 font-mono text-[10px] text-[var(--color-text-tertiary)]">
              {d.status}
              {d.created_at ? ` · ${formatRelativeTime(d.created_at)}` : ""}
            </span>
          </A>
        </li>
      ))}
    </ul>
  );
}
