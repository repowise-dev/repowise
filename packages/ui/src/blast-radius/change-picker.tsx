"use client";

import type { ChangeCandidate, ChangeCandidatesResponse } from "@repowise-dev/types/blast-radius";
import { formatRelativeTimeOrNull } from "../lib/format";
import { MICRO } from "../health/tokens";
import { cn } from "../lib/cn";

const GROUPS: { kind: ChangeCandidate["kind"]; title: string }[] = [
  { kind: "working_tree", title: "In progress" },
  { kind: "branch", title: "Branches" },
  { kind: "commit", title: "Recent commits" },
];

function meta(c: ChangeCandidate): string {
  const parts: string[] = [];
  if (c.kind === "branch" && c.ahead !== null) parts.push(`${c.ahead} commit${c.ahead === 1 ? "" : "s"} ahead`);
  if (c.files_changed !== null) parts.push(`${c.files_changed} file${c.files_changed === 1 ? "" : "s"}`);
  const when = formatRelativeTimeOrNull(c.committed_at, "");
  if (when) parts.push(when);
  if (c.is_current && c.kind === "branch") parts.push("checked out");
  return parts.join(" · ");
}

/**
 * The changes to pick from, grouped by where they are: work in progress,
 * branches ahead of the default branch, recent commits. One click selects.
 * A server without a checkout offers commits only and says so.
 */
export function ChangePicker({
  data,
  selectedId,
  onSelect,
}: {
  data: ChangeCandidatesResponse;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <nav aria-label="Changes" className="space-y-4">
      {GROUPS.map(({ kind, title }) => {
        const rows = data.changes.filter((c) => c.kind === kind);
        if (rows.length === 0) {
          if (kind === "working_tree" || (kind === "branch" && !data.working_tree_supported)) return null;
          return (
            <div key={kind} className="space-y-1">
              <p className={MICRO}>{title}</p>
              <p className="text-xs text-[var(--color-text-tertiary)]">
                {kind === "branch" ? "No branch is ahead of the default branch." : "No indexed commits yet."}
              </p>
            </div>
          );
        }
        return (
          <div key={kind} className="space-y-1">
            <p className={MICRO}>{title}</p>
            <ul className="space-y-0.5">
              {rows.map((c) => {
                const active = c.id === selectedId;
                return (
                  <li key={c.id}>
                    <button
                      type="button"
                      aria-current={active ? "true" : undefined}
                      onClick={() => onSelect(c.id)}
                      className={cn(
                        "w-full rounded-md px-2.5 py-1.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]",
                        active
                          ? "bg-[var(--color-bg-elevated)] shadow-[inset_2px_0_0_var(--color-accent-primary)]"
                          : "hover:bg-[var(--color-bg-elevated)]",
                      )}
                    >
                      <span
                        className={cn(
                          "block truncate text-xs",
                          kind === "working_tree" ? "font-medium" : "font-mono",
                          active ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-secondary)]",
                        )}
                      >
                        {c.label}
                      </span>
                      {c.title && (
                        <span className="block truncate text-xs text-[var(--color-text-secondary)]">{c.title}</span>
                      )}
                      <span className="block truncate text-[11px] text-[var(--color-text-tertiary)]">{meta(c)}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
      {!data.working_tree_supported && (
        <p className="text-[11px] text-[var(--color-text-tertiary)]">
          Uncommitted work and branches show when Repowise runs next to the checkout.
        </p>
      )}
    </nav>
  );
}
