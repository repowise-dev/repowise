"use client";

import { useMemo, useState } from "react";
import { ChevronDown, GitBranch, GitCommitHorizontal, Pencil } from "lucide-react";
import type { ChangeCandidate, ChangeCandidateKind, ChangeCandidatesResponse } from "@repowise-dev/types/blast-radius";
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover";
import { Spinner } from "../ui/spinner";
import { Segmented, type SegmentOption } from "../shared/segmented";
import { formatRelativeTimeOrNull } from "../lib/format";
import { cn } from "../lib/cn";

const KIND_LABEL: Record<ChangeCandidateKind, string> = {
  working_tree: "Working tree",
  branch: "Branches",
  commit: "Commits",
};

const KIND_ICON: Record<ChangeCandidateKind, typeof GitBranch> = {
  working_tree: Pencil,
  branch: GitBranch,
  commit: GitCommitHorizontal,
};

const SHOWN = 5;
const RECENT_MS = 7 * 24 * 60 * 60 * 1000;

function plural(n: number, word: string): string {
  return `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
}

/** The line under a change's name: what it holds and when. */
export function changeMeta(c: ChangeCandidate): string {
  const parts: string[] = [];
  if (c.kind === "commit") parts.push(c.label);
  if (c.kind === "branch" && c.ahead !== null) parts.push(`${plural(c.ahead, "commit")} ahead`);
  if (c.files_changed !== null) parts.push(plural(c.files_changed, "file"));
  const when = formatRelativeTimeOrNull(c.committed_at, "");
  if (when) parts.push(when);
  if (c.kind === "branch" && c.is_current) parts.push("checked out");
  if (c.kind === "branch" && c.pushed === false) parts.push("local only");
  return parts.join(", ");
}

/** A change's name: a commit by its subject, anything else by its label. */
export function changeName(c: ChangeCandidate): string {
  return c.kind === "commit" ? (c.title ?? c.label) : c.label;
}

/** Recent work up front; a branch idle for a week is usually done or abandoned. */
function isRecent(c: ChangeCandidate, now: number): boolean {
  if (c.kind !== "branch" || c.is_current) return true;
  const at = c.committed_at ? Date.parse(c.committed_at) : NaN;
  return !Number.isNaN(at) && now - at < RECENT_MS;
}

function ChangeRow({ c, active, onSelect }: { c: ChangeCandidate; active: boolean; onSelect: () => void }) {
  return (
    <li>
      <button
        type="button"
        aria-current={active ? "true" : undefined}
        onClick={onSelect}
        title={c.title ?? c.label}
        className={cn(
          "w-full rounded-md px-2 py-1.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]",
          active ? "bg-[var(--color-bg-selected,var(--color-bg-elevated))]" : "hover:bg-[var(--color-bg-elevated)]",
        )}
      >
        <span
          className={cn(
            "block truncate text-xs text-[var(--color-text-primary)]",
            c.kind === "branch" ? "font-mono" : "font-medium",
            active && "font-semibold",
          )}
        >
          {changeName(c)}
        </span>
        {c.kind === "branch" && c.title && (
          <span className="block truncate text-xs text-[var(--color-text-secondary)]">{c.title}</span>
        )}
        <span className="block truncate text-[11px] text-[var(--color-text-tertiary)]">{changeMeta(c)}</span>
      </button>
    </li>
  );
}

function ChangeList({
  rows,
  selectedId,
  onSelect,
}: {
  rows: ChangeCandidate[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [all, setAll] = useState(false);
  const now = Date.now();
  const recent = rows.filter((c) => isRecent(c, now));
  const lead = (recent.length > 0 ? recent : rows).slice(0, SHOWN);
  // The picked change stays visible even when it sits past the cut.
  const shown = all ? rows : rows.filter((c) => lead.includes(c) || c.id === selectedId);
  const hidden = rows.length - shown.length;
  if (rows.length === 0) return <p className="px-2 py-3 text-xs text-[var(--color-text-tertiary)]">Nothing matches.</p>;
  return (
    <>
      <ul className="max-h-72 space-y-0.5 overflow-y-auto">
        {shown.map((c) => (
          <ChangeRow key={c.id} c={c} active={c.id === selectedId} onSelect={() => onSelect(c.id)} />
        ))}
      </ul>
      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setAll(true)}
          className="px-2 text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
        >
          Show {hidden} older
        </button>
      )}
    </>
  );
}

function kindOptions(data: ChangeCandidatesResponse): SegmentOption<ChangeCandidateKind>[] {
  return (Object.keys(KIND_LABEL) as ChangeCandidateKind[]).map((kind) => {
    const n = data.changes.filter((c) => c.kind === kind).length;
    let disabledReason: string | undefined;
    if (n === 0 && kind !== "commit" && !data.working_tree_supported)
      disabledReason = "Shows when Repowise runs next to the checkout";
    else if (n === 0)
      disabledReason =
        kind === "working_tree" ? "No uncommitted changes" : kind === "branch" ? "No branch is ahead of the default branch" : "No indexed commits yet";
    return { value: kind, label: KIND_LABEL[kind], count: n > 0 ? String(n) : undefined, disabledReason };
  });
}

/**
 * Which change is under review, as one compact control: the picked change's
 * name, opening to its siblings by kind (working tree, branches, commits)
 * with a search. Recent, pushed branches lead; the rest are one click away.
 * A server without a checkout offers commits only and says why.
 */
export function ChangePicker({
  data,
  loading = false,
  selected,
  onSelect,
}: {
  data: ChangeCandidatesResponse | undefined;
  loading?: boolean;
  /** The change under review, listed or read off a link. */
  selected: ChangeCandidate | undefined;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<ChangeCandidateKind | null>(null);
  const activeKind = kind ?? selected?.kind ?? data?.changes[0]?.kind ?? "commit";
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data?.changes ?? []).filter(
      (c) => c.kind === activeKind && (!q || `${c.label} ${c.title ?? ""}`.toLowerCase().includes(q)),
    );
  }, [data, activeKind, query]);
  const Icon = selected ? KIND_ICON[selected.kind] : GitBranch;

  if (loading && !selected)
    return (
      <span className="inline-flex h-8 items-center gap-2 rounded-md border border-[var(--color-border-default)] px-2.5 text-xs text-[var(--color-text-secondary)]">
        <Spinner size="xs" />
        Reading your branches and commits...
      </span>
    );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={selected ? `Change under review: ${changeName(selected)}. Pick another` : "Pick a change to review"}
          className="inline-flex h-8 min-w-0 max-w-full items-center gap-2 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2.5 text-left text-xs hover:border-[var(--color-border-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
        >
          <Icon className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" aria-hidden />
          <span className={cn("min-w-0 truncate font-medium text-[var(--color-text-primary)]", selected?.kind === "branch" && "font-mono")}>
            {selected ? changeName(selected) : "Pick a change"}
          </span>
          {loading && <Spinner size="xs" />}
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(26rem,calc(100vw-2rem))] space-y-2 p-2">
        {data ? (
          <>
            <Segmented label="Kind of change" value={activeKind} options={kindOptions(data)} onChange={setKind} />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={`Search ${KIND_LABEL[activeKind].toLowerCase()}`}
              aria-label="Search changes"
              className="w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 py-1 text-xs text-[var(--color-text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-focus-ring)]"
            />
            <ChangeList
              key={`${activeKind}:${query}`}
              rows={rows}
              selectedId={selected?.id ?? null}
              onSelect={(id) => {
                setOpen(false);
                onSelect(id);
              }}
            />
          </>
        ) : (
          <p className="px-2 py-3 text-xs text-[var(--color-text-tertiary)]">Reading your branches and commits...</p>
        )}
      </PopoverContent>
    </Popover>
  );
}
