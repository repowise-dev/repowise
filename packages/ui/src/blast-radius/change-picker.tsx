"use client";

import { useState } from "react";
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

const SHOWN = 3;

/** Pushed branches first; a local-only branch is often scratch work. Order is otherwise kept (newest first). */
function ordered(rows: ChangeCandidate[]): ChangeCandidate[] {
  return [...rows.filter((c) => c.pushed !== false), ...rows.filter((c) => c.pushed === false)];
}

function ChangeRow({ c, active, onSelect }: { c: ChangeCandidate; active: boolean; onSelect: () => void }) {
  const scratch = c.kind === "branch" && c.pushed === false;
  // A commit is named by its subject; its sha rides in the meta line.
  const name = c.kind === "commit" ? (c.title ?? c.label) : c.label;
  const line = c.kind === "commit" ? `${c.label} · ${meta(c)}` : meta(c);
  return (
    <li>
      <button
        type="button"
        aria-current={active ? "true" : undefined}
        onClick={onSelect}
        title={c.title ?? c.label}
        className={cn(
          "w-full rounded-md px-2.5 py-1.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]",
          active
            ? "bg-[var(--color-bg-elevated)] shadow-[inset_2px_0_0_var(--color-accent-primary)]"
            : "hover:bg-[var(--color-bg-elevated)]",
          scratch && !active && "opacity-60",
        )}
      >
        <span
          className={cn(
            "flex items-center gap-1.5 truncate text-xs",
            c.kind === "branch" ? "font-mono" : "font-medium",
            active ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-secondary)]",
          )}
        >
          <span className="truncate">{name}</span>
          {scratch && (
            <span className="shrink-0 rounded border border-[var(--color-border-default)] px-1 font-sans text-[9px] uppercase tracking-wide text-[var(--color-text-tertiary)]" title="Not on the remote">
              local
            </span>
          )}
        </span>
        <span className="block truncate text-[11px] text-[var(--color-text-tertiary)]">{line}</span>
      </button>
    </li>
  );
}

function ChangeGroup({
  title,
  rows,
  selectedId,
  onSelect,
}: {
  title: string;
  rows: ChangeCandidate[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [all, setAll] = useState(false);
  const list = ordered(rows);
  // The picked change stays visible even when it sits past the cut.
  const shown = all ? list : list.filter((c, i) => i < SHOWN || c.id === selectedId);
  const hidden = list.length - shown.length;
  return (
    <div className="space-y-1">
      <p className={MICRO}>{title}</p>
      <ul className="space-y-0.5">
        {shown.map((c) => (
          <ChangeRow key={c.id} c={c} active={c.id === selectedId} onSelect={() => onSelect(c.id)} />
        ))}
      </ul>
      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setAll(true)}
          className="px-2.5 text-[11px] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
        >
          Show {hidden} more
        </button>
      )}
    </div>
  );
}

/**
 * The changes to pick from, grouped by where they are: work in progress,
 * branches ahead of the default branch, recent commits. A few per group,
 * the rest one click away. One click selects. A server without a checkout
 * offers commits only and says so.
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
        if (rows.length > 0)
          return <ChangeGroup key={kind} title={title} rows={rows} selectedId={selectedId} onSelect={onSelect} />;
        if (kind === "working_tree" || (kind === "branch" && !data.working_tree_supported)) return null;
        return (
          <div key={kind} className="space-y-1">
            <p className={MICRO}>{title}</p>
            <p className="text-xs text-[var(--color-text-tertiary)]">
              {kind === "branch" ? "No branch is ahead of the default branch." : "No indexed commits yet."}
            </p>
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
