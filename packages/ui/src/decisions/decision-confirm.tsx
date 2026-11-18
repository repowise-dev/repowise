import * as React from "react";

import { FIELD_CLASS } from "../ui/input";
import { cn } from "../lib/cn";

/**
 * What stands between a decision and being accepted, in the words a reader
 * acts on. `decisionAcceptanceBlockers` (types) is the source of truth for
 * *whether*; this only words each gap and says where to fix it, so the detail
 * page, the review queue and its drawer never show the contract's own terms.
 */

/** What an accept carries beyond the record. */
export interface DecisionAcceptOptions {
  /** Why the decision holds, typed where the record states none. */
  reason?: string;
}

/** The reason gap, the one a reader can fill in place with a sentence. */
export function isReasonGap(gap: string): boolean {
  return gap.startsWith("no rationale");
}

/**
 * One gap as a plain sentence with its next step. `id` names the record in
 * the CLI fallback; `onRecordPage` is false where the scope editor is a click
 * away. An unknown gap is shown as the server worded it.
 */
export function describeGap(gap: string, id: string, onRecordPage = true): string {
  const short = id.slice(0, 8);
  if (isReasonGap(gap)) return "It doesn't say why it holds. Add a reason.";
  if (gap.startsWith("no decision text")) {
    return `It doesn't say what was chosen. Add it with repowise decision confirm ${short} --decision "...", or dismiss it.`;
  }
  if (gap.startsWith("no scope")) {
    return `It names no files or modules. Add them under Governance scope${onRecordPage ? "" : " on the full record"}.`;
  }
  if (gap.startsWith("no evidence")) {
    return `It doesn't point to a commit or file. Add one with repowise decision confirm ${short} --evidence <commit>, or dismiss it.`;
  }
  return gap.charAt(0).toUpperCase() + gap.slice(1) + (/[.!?]$/.test(gap) ? "" : ".");
}

const KNOWN_GAP = /^no (rationale|decision text|scope|evidence)/;

/** A gap this module words, as against a one-off sentence from the server. */
export function isKnownGap(gap: string): boolean {
  return KNOWN_GAP.test(gap);
}

/** A short form for a table row, where the drawer holds the full sentence. */
export function shortGap(gap: string): string {
  if (isReasonGap(gap)) return "needs a reason";
  if (gap.startsWith("no decision text")) return "doesn't say what was chosen";
  if (gap.startsWith("no scope")) return "names no files";
  if (gap.startsWith("no evidence")) return "no commit or file behind it";
  return gap;
}

/**
 * The gaps a refused confirm named, from either host's error: the OSS client
 * keeps them on `blockers`, the hosted client keeps the raw `detail`. Empty
 * when the error carries none, so the caller falls back to its message.
 */
export function refusedGaps(err: unknown): string[] {
  const e = err as { blockers?: unknown; detail?: unknown } | null;
  const lists = [e?.blockers, (e?.detail as { blockers?: unknown } | null)?.blockers];
  for (const list of lists) {
    if (Array.isArray(list)) {
      const gaps = list.filter((b): b is string => typeof b === "string" && b.trim() !== "");
      if (gaps.length) return gaps;
    }
  }
  return [];
}

/**
 * "Why does this hold?", filled in place when the reason is the only thing a
 * decision is missing. What is typed is sent as `reason` with the confirm.
 */
export function ReasonField({
  id,
  value,
  onChange,
  disabled,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean | undefined;
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-xs font-medium text-[var(--color-text-secondary)]">
        Why does this hold?
      </label>
      <textarea
        id={id}
        rows={2}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        placeholder="One or two sentences, e.g. what breaks without it."
        className={cn(FIELD_CLASS, "block w-full max-w-xl px-3 py-2 text-sm")}
      />
      <p className="text-xs text-[var(--color-text-tertiary)]">Saved as the reason with the decision.</p>
    </div>
  );
}

/** What a reader must do before Confirm works, as plain sentences. */
export function GapList({
  gaps,
  id,
  onRecordPage = true,
}: {
  gaps: string[];
  id: string;
  onRecordPage?: boolean;
}) {
  if (gaps.length === 0) return null;
  return (
    <div className="space-y-1 text-xs text-[var(--color-text-secondary)]">
      <p className="font-medium">Before you can confirm:</p>
      <ul className="list-disc space-y-0.5 pl-4">
        {gaps.map((g) => (
          <li key={g}>{describeGap(g, id, onRecordPage)}</li>
        ))}
      </ul>
    </div>
  );
}
