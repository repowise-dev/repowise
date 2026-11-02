import * as React from "react";
import { GitPullRequest } from "lucide-react";
import type { DecisionRecord } from "@repowise-dev/types/decisions";

import { cn } from "../lib/cn";
import { formatDate, parseDateOrNull } from "../lib/format";

type OriginFields = Pick<
  DecisionRecord,
  | "source"
  | "source_url"
  | "currency"
  | "status"
  | "accepter"
  | "accepter_kind"
  | "accepter_session"
  | "auto_accepted"
  | "applied_count"
  | "last_applied_at"
  | "last_applied_pr"
  | "may_be_stale"
>;

/** The GitHub login in a `github:<login>` accepter, else null. */
export function githubLogin(accepter: string | null | undefined): string | null {
  const m = /^github:([A-Za-z0-9-]+)$/.exec((accepter ?? "").trim());
  return m ? m[1]! : null;
}

/** Only a web link is drawn as one; anything else is not a place to go. */
function safeHref(url: string | null | undefined): string | null {
  return url && /^https:\/\/\S+$/.test(url) ? url : null;
}

/** A recorded session or key label, clipped so a long one never swamps the line. */
const SESSION_MAX = 40;

function clip(text: string, max = SESSION_MAX): string {
  // By code points, so the cut never splits a surrogate pair.
  const chars = Array.from(text);
  return chars.length > max ? `${chars.slice(0, max - 1).join("")}\u2026` : text;
}

/**
 * Who accepted a decision, when that can be said in plain words: an agent
 * ("Accepted by your agent", plus the session or key label when one was
 * recorded) or a GitHub login, in the bot's own words. Null for a record
 * nobody accepted, or one a person accepted outside GitHub.
 */
export function acceptedLine(d: OriginFields): string | null {
  const accepted = d.currency != null ? d.currency !== "dismissed" : d.status === "active";
  if (!accepted) return null;
  if (d.accepter_kind === "agent") {
    const session = d.accepter_session?.trim();
    return session ? `Accepted by your agent (${clip(session)})` : "Accepted by your agent";
  }
  const login = githubLogin(d.accepter);
  if (!login) return null;
  return d.auto_accepted ? `Accepted automatically from @${login}'s reply` : `Accepted by @${login}`;
}

/** Who accepted it, as the quiet line beside the record's other marks. */
export function DecisionAcceptedLine({ decision, className }: { decision: OriginFields; className?: string }) {
  const who = acceptedLine(decision);
  if (!who) return null;
  return <span className={cn("text-xs text-[var(--color-text-tertiary)]", className)}>{who}</span>;
}

/**
 * "From PR review" plus who accepted it. Where a decision came from a review
 * thread, the thread is the reason to trust it, so the mark links there. Who
 * accepted it shows for any source; `accepter={false}` leaves it to a host
 * that draws it elsewhere, beside its Undo.
 */
export function DecisionOrigin({
  decision: d,
  accepter = true,
  className,
}: {
  decision: OriginFields;
  accepter?: boolean;
  className?: string;
}) {
  const fromReview = d.source === "pr_review";
  const who = accepter ? acceptedLine(d) : null;
  if (!fromReview && !who) return null;
  const href = safeHref(d.source_url);
  const mark = (
    <>
      <GitPullRequest className="h-3 w-3" aria-hidden />
      From PR review
    </>
  );
  const markCls =
    "inline-flex items-center gap-1 rounded-full border border-[var(--color-border-default)] px-2 py-0.5 text-xs text-[var(--color-text-secondary)]";
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-x-2 gap-y-1", className)}>
      {fromReview &&
        (href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            // Its own target inside a clickable row.
            onClick={(e) => e.stopPropagation()}
            className={cn(markCls, "hover:text-[var(--color-text-primary)] hover:underline")}
          >
            {mark}
          </a>
        ) : (
          <span className={markCls}>{mark}</span>
        ))}
      {who && <span className="text-xs text-[var(--color-text-tertiary)]">{who}</span>}
    </span>
  );
}

/**
 * "Applied in 4 reviews · last on PR #N", plus a quiet "may be out of
 * date" when the code moved since. Null when the host sends none of it.
 */
export function appliedLine(d: OriginFields): string | null {
  const parts: string[] = [];
  const n = d.applied_count ?? 0;
  if (n > 0) parts.push(`Applied in ${n} ${n === 1 ? "review" : "reviews"}`);
  if (d.last_applied_pr != null) {
    parts.push(`last on PR #${d.last_applied_pr}`);
  } else if (parseDateOrNull(d.last_applied_at)) {
    parts.push(`last on ${formatDate(d.last_applied_at)}`);
  }
  if (d.may_be_stale) parts.push("may be out of date, its code changed since");
  if (parts.length === 0) return null;
  const line = parts.join(" · ");
  return line.charAt(0).toUpperCase() + line.slice(1);
}

export function DecisionAppliedLine({ decision, className }: { decision: OriginFields; className?: string }) {
  const line = appliedLine(decision);
  if (!line) return null;
  return <p className={cn("text-xs text-[var(--color-text-tertiary)]", className)}>{line}</p>;
}
