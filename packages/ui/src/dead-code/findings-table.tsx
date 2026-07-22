"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { toast } from "sonner";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../ui/tabs";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Slider } from "../ui/slider";
import { Switch } from "../ui/switch";
import { TableSkeleton } from "../shared/loading-skeletons";
import { ConfirmDialog } from "../ui/confirm-dialog";
import { EmptyState } from "../shared/empty-state";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { toFriendlyMessage } from "../lib/errors";
import { AiPromptButton } from "../health/ai-prompt-button";
import {
  FindingIdentity,
  FindingConfidence,
  FindingSafety,
  FindingRowActions,
  DEAD_CODE_STATUS_LABELS,
} from "./finding-cells";
import {
  DEAD_CODE_CONFIDENCE,
  type DeadCodeFinding,
  type DeadCodeStatus,
} from "@repowise-dev/types/dead-code";

/**
 * Display names and tab order for the kinds we know about. Any other kind the
 * detector emits still gets a tab, labelled from its own identifier — a hard
 * allowlist here once hid every `unused_internal` finding, which the summary
 * card and the breakdown grid were counting all along.
 */
const KIND_LABELS: Record<string, string> = {
  unreachable_file: "Unreachable Files",
  unused_export: "Unused Exports",
  unused_internal: "Unused Internals",
  zombie_package: "Zombie Packages",
};

const KIND_ORDER = Object.keys(KIND_LABELS);

/** "unused_internal" -> "Unused Internals" for kinds we have no name for. */
function labelForKind(kind: string): string {
  const known = KIND_LABELS[kind];
  if (known) return known;
  return kind
    .split(/[_\-\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

type SortKey = "path" | "confidence" | "owner" | "lines";

/** Sort value per column; strings compare with localeCompare, numbers subtract. */
function sortValue(f: DeadCodeFinding, key: SortKey): string | number {
  switch (key) {
    case "path":
      return `${f.file_path} ${f.symbol_name ?? ""}`;
    case "owner":
      return f.primary_owner ?? "";
    case "lines":
      return f.lines;
    case "confidence":
      return f.confidence;
  }
}

export interface FindingsTableProps {
  /** One status slice; the table filters by kind / confidence / safety client-side. */
  findings: DeadCodeFinding[];
  /** Injected mutation — host owns the API call, optimistic toast + undo. */
  onPatch: (id: string, patch: { status: DeadCodeStatus }) => Promise<DeadCodeFinding>;
  /** Injected bulk resolve — host owns the toast/partial-failure messaging. Returns the ids that actually resolved. */
  onBulkResolve?: (ids: string[]) => Promise<string[]>;
  /** Href for a file detail page; makes the path a link and the row clickable. */
  fileHref?: ((path: string) => string) | undefined;
  /** Client-side navigation for a row click. */
  onNavigate?: ((href: string) => void) | undefined;
  /** Href for the dependency graph focused on a file; omit to hide the action. */
  graphHref?: ((path: string) => string) | undefined;
  /** Open an AI cleanup prompt for the given findings (one row, or the selection). */
  onGeneratePrompt?: ((ids: string[]) => void) | undefined;
  /**
   * Which status slice is on screen. The host owns both the fetch and the
   * control that switches it, because a clean repository replaces this table
   * with an empty state and a control living in here would go with it.
   */
  status?: DeadCodeStatus | undefined;
  isLoading?: boolean;
}

/**
 * Canonical interactive dead-code table: kind tabs, a search box, sortable
 * columns, a confidence slider (the single confidence control for the spine),
 * cleanup-ready filter, bulk resolve, and per-row status actions.
 *
 * Built on the shared `ResponsiveTable` so it collapses to stacked cards on
 * small screens and windows its rows — the fetch is capped at 500, which is
 * well past the point where rendering every `<tr>` costs a visible frame.
 *
 * Pure presentation — the host injects the data slice and the patch/bulk
 * mutations, so this propagates via the package.
 */
export function FindingsTable({
  findings,
  onPatch,
  onBulkResolve,
  fileHref,
  onNavigate,
  graphHref,
  onGeneratePrompt,
  status = "open",
  isLoading,
}: FindingsTableProps) {
  const [activeTab, setActiveTab] = useState<string | null>(null);
  // The slider floor is the server's own `min_confidence` default: nothing
  // below it is ever fetched, so a lower floor would be a control over nothing.
  const [minConfidence, setMinConfidence] = useState<number>(DEAD_CODE_CONFIDENCE.MEDIUM);
  const [safeOnly, setSafeOnly] = useState(false);
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("confidence");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkPending, setBulkPending] = useState(false);
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false);

  // Tabs come from the unfiltered slice so the set stays put while the
  // confidence slider empties a bucket, instead of tabs appearing and
  // vanishing under the pointer.
  const tabs = useMemo(() => {
    const present = new Set(findings.map((f) => f.kind));
    const known = KIND_ORDER.filter((k) => present.has(k));
    const extra = [...present].filter((k) => !KIND_LABELS[k]).sort();
    return [...known, ...extra].map((value) => ({ value, label: labelForKind(value) }));
  }, [findings]);

  const byKind = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const buckets: Record<string, DeadCodeFinding[]> = {};
    for (const t of tabs) buckets[t.value] = [];
    for (const f of findings) {
      if (f.confidence < minConfidence) continue;
      if (safeOnly && !f.safe_to_delete) continue;
      if (
        needle &&
        !f.file_path.toLowerCase().includes(needle) &&
        !(f.symbol_name ?? "").toLowerCase().includes(needle) &&
        !(f.primary_owner ?? "").toLowerCase().includes(needle) &&
        !(f.reason ?? "").toLowerCase().includes(needle)
      ) {
        continue;
      }
      buckets[f.kind]?.push(f);
    }
    return buckets;
  }, [findings, tabs, minConfidence, safeOnly, query]);

  // The active tab can disappear when the slice changes (a refetch, a resolved
  // last row); fall back to the first tab rather than rendering nothing.
  const effectiveTab =
    activeTab && tabs.some((t) => t.value === activeTab) ? activeTab : (tabs[0]?.value ?? null);

  // Rows arrived in server order (confidence desc) with no way to reorder them.
  // The default keeps that order so the rewrite does not silently reshuffle
  // anyone's table; every column can now take over.
  const current = useMemo(() => {
    const rows = effectiveTab ? (byKind[effectiveTab] ?? []) : [];
    const dir = sortOrder === "asc" ? 1 : -1;
    // Copy before sorting: never mutate the caller's findings array in place.
    return [...rows].sort((a, b) => {
      const av = sortValue(a, sortKey);
      const bv = sortValue(b, sortKey);
      const cmp =
        typeof av === "string" && typeof bv === "string" ? av.localeCompare(bv) : Number(av) - Number(bv);
      // Ties would otherwise fall back to bucket order, which shifts as filters
      // change; the id is stable and unique.
      return cmp !== 0 ? cmp * dir : a.id.localeCompare(b.id);
    });
  }, [byKind, effectiveTab, sortKey, sortOrder]);

  const toggleSort = (key: string) => {
    if (key === sortKey) {
      setSortOrder((o) => (o === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(key as SortKey);
    // Paths read best A→Z; every metric reads best biggest-first.
    setSortOrder(key === "path" || key === "owner" ? "asc" : "desc");
  };

  // Selection is scoped to what is on screen. Without this, raising the
  // confidence slider after selecting rows would resolve findings the user can
  // no longer see.
  const visibleSelected = useMemo(() => {
    const visible = new Set(current.map((f) => f.id));
    return Array.from(selected).filter((id) => visible.has(id));
  }, [current, selected]);
  const selectedCount = visibleSelected.length;

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const allVisibleSelected = current.length > 0 && selectedCount === current.length;

  const toggleSelectAll = () => {
    setSelected(allVisibleSelected ? new Set() : new Set(current.map((f) => f.id)));
  };

  const resetFilters = () => {
    setMinConfidence(DEAD_CODE_CONFIDENCE.MEDIUM);
    setSafeOnly(false);
    setQuery("");
  };

  const filtersActive = minConfidence > DEAD_CODE_CONFIDENCE.MEDIUM || safeOnly || query.trim() !== "";

  const resolveSelected = async () => {
    if (!onBulkResolve) return;
    setBulkPending(true);
    try {
      // Send the visible intersection, which is what the confirm dialog counted.
      await onBulkResolve(visibleSelected);
      setSelected(new Set());
      setBulkConfirmOpen(false);
    } catch (err) {
      // The prop contract does not promise a host that swallows its own
      // failures; without this the dialog wedges open on a rejection.
      toast.error(`Couldn't resolve findings: ${toFriendlyMessage(err)}`);
    } finally {
      setBulkPending(false);
    }
  };

  const columns = useMemo<ResponsiveColumn<DeadCodeFinding>[]>(() => {
    const checkbox = (f: DeadCodeFinding) => (
      <span onClick={(e) => e.stopPropagation()}>
        <input
          type="checkbox"
          checked={selected.has(f.id)}
          onChange={() => toggleSelect(f.id)}
          aria-label={`Select finding ${f.file_path}`}
          className="rounded border-[var(--color-border-default)]"
        />
      </span>
    );

    return [
      {
        key: "select",
        header: (
          <span onClick={(e) => e.stopPropagation()}>
            <input
              type="checkbox"
              checked={allVisibleSelected}
              // A partial selection read as "nothing selected" before, so
              // clicking through it silently discarded the user's picks.
              ref={(el) => {
                if (el) el.indeterminate = selectedCount > 0 && !allVisibleSelected;
              }}
              onChange={toggleSelectAll}
              aria-label="Select all findings"
              className="rounded border-[var(--color-border-default)]"
            />
          </span>
        ),
        headerClassName: "w-8",
        // Stacked cards give the first visible column to the card title, so the
        // checkbox rides along with the identity cell there instead.
        hideInCard: true,
        render: checkbox,
      },
      {
        key: "path",
        header: "File / Symbol",
        sortable: true,
        cellClassName: "min-w-[200px] max-w-[480px]",
        render: (f) => (
          <FindingIdentity
            finding={f}
            href={fileHref?.(f.file_path)}
            onNavigate={onNavigate}
          />
        ),
        mobileRender: (f) => (
          <span className="flex items-start gap-2">
            {checkbox(f)}
            <FindingIdentity
              finding={f}
              href={fileHref?.(f.file_path)}
              onNavigate={onNavigate}
            />
          </span>
        ),
      },
      {
        key: "confidence",
        header: "Confidence",
        mobileLabel: "Confidence",
        sortable: true,
        headerClassName: "w-24",
        render: (f) => <FindingConfidence finding={f} />,
      },
      {
        key: "owner",
        header: "Owner",
        sortable: true,
        priority: 2,
        render: (f) => (
          <span className="text-xs text-[var(--color-text-secondary)]">
            {f.primary_owner ?? "—"}
          </span>
        ),
      },
      {
        key: "lines",
        header: "Lines",
        sortable: true,
        priority: 2,
        align: "right",
        headerClassName: "w-16",
        render: (f) => (
          <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">{f.lines}</span>
        ),
      },
      {
        key: "safety",
        header: "Safety",
        mobileLabel: "Safety",
        // The badge's tooltip is the only place risk_factors surface, so keep
        // it on tablet widths where the old markup showed it.
        priority: 2,
        headerClassName: "w-20",
        render: (f) => <FindingSafety finding={f} />,
      },
      {
        key: "actions",
        header: <span className="sr-only">Actions</span>,
        mobileLabel: "Actions",
        headerClassName: "w-36",
        align: "right",
        render: (f) => (
          <FindingRowActions
            finding={f}
            onPatch={onPatch}
            graphHref={graphHref}
            {...(onGeneratePrompt
              ? { onGeneratePrompt: (id: string) => onGeneratePrompt([id]) }
              : {})}
          />
        ),
      },
    ];
    // `selected` and `allVisibleSelected` drive the checkbox state; the rest are
    // the injected callbacks.
  }, [
    selected,
    selectedCount,
    allVisibleSelected,
    fileHref,
    onNavigate,
    graphHref,
    onGeneratePrompt,
    onPatch,
    current,
  ]);

  const openFile =
    fileHref && onNavigate
      ? (f: DeadCodeFinding) => onNavigate(fileHref(f.file_path))
      : undefined;

  return (
    <div className="space-y-4">
      {/* Controls — the confidence slider is the only confidence axis. */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-tertiary)]" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search path, symbol, owner, reason…"
            aria-label="Search findings"
            className="h-8 w-full pl-8 text-xs sm:w-72"
          />
        </div>
        <div className="flex items-center gap-2">
          {/* A live value readout, not a <label>: the thing it describes is a
              Radix span with role="slider", which htmlFor cannot reach. The
              accessible name rides on the thumb's aria-label instead. */}
          <span className="text-xs text-[var(--color-text-secondary)]">
            Min confidence: {Math.round(minConfidence * 100)}%
          </span>
          <Slider
            min={DEAD_CODE_CONFIDENCE.MEDIUM}
            max={1}
            step={0.05}
            value={[minConfidence]}
            onValueChange={([v]) => setMinConfidence(v ?? DEAD_CODE_CONFIDENCE.MEDIUM)}
            aria-label="Minimum confidence"
            className="w-28"
          />
        </div>
        <label className="flex cursor-pointer select-none items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
          <Switch checked={safeOnly} onCheckedChange={setSafeOnly} />
          Cleanup-ready only
        </label>

        {/* Bulk resolve only makes sense on findings that are still open. */}
        {onBulkResolve && status === "open" && selectedCount > 0 && (
          <Button
            size="sm"
            variant="outline"
            disabled={bulkPending}
            onClick={() => setBulkConfirmOpen(true)}
            className="text-[var(--color-success)] border-[var(--color-success)]/30 hover:bg-[var(--color-success)]/10"
          >
            {bulkPending ? "Resolving…" : `Resolve ${selectedCount} selected`}
          </Button>
        )}

        {onGeneratePrompt && selectedCount > 0 && (
          <AiPromptButton
            label={`AI prompt for ${selectedCount} selected`}
            onClick={() => onGeneratePrompt(visibleSelected)}
          />
        )}
      </div>
      <ConfirmDialog
        open={bulkConfirmOpen}
        onOpenChange={setBulkConfirmOpen}
        title={`Resolve ${selectedCount} finding${selectedCount === 1 ? "" : "s"}?`}
        description="This will mark each selected finding as resolved."
        confirmLabel="Resolve all"
        loading={bulkPending}
        onConfirm={resolveSelected}
      />

      {isLoading && findings.length === 0 ? (
        <TableSkeleton className="mt-2" />
      ) : tabs.length === 0 ? (
        <EmptyState
          title="No findings"
          description={`No ${DEAD_CODE_STATUS_LABELS[status].toLowerCase()} dead-code findings for this repository.`}
        />
      ) : (
        <Tabs
          // Non-null in this branch: tabs is non-empty, so effectiveTab resolved.
          value={effectiveTab ?? ""}
          onValueChange={(v) => {
            setActiveTab(v);
            // Selection is per-kind; clearing on tab change keeps "Resolve N
            // selected" and the select-all checkbox scoped to the visible rows.
            setSelected(new Set());
          }}
        >
          <TabsList>
            {tabs.map((t) => (
              <TabsTrigger key={t.value} value={t.value}>
                {t.label}
                {(byKind[t.value]?.length ?? 0) > 0 && (
                  <span className="ml-1.5 text-xs text-[var(--color-text-tertiary)]">
                    {byKind[t.value]?.length}
                  </span>
                )}
              </TabsTrigger>
            ))}
          </TabsList>

          {/* One content panel for the active tab only. Rendering a panel per tab
              and filling each with the *active* tab's rows happened to work
              because Radix unmounts inactive content, which is a trap waiting on
              whoever adds forceMount. */}
          <TabsContent value={effectiveTab ?? ""} className="mt-2">
            <ResponsiveTable<DeadCodeFinding>
              columns={columns}
              rows={current}
              rowKey={(f) => f.id}
              caption="Dead code findings"
              stacked="sm"
              sortField={sortKey}
              sortOrder={sortOrder}
              onSort={toggleSort}
              virtualize={{ estimateRowHeight: 56, estimateCardHeight: 96 }}
              // The hover class the primitive applies is a different Tailwind
              // variant, so it survives the merge and would blank the selected
              // tint the moment the pointer lands on a selected row.
              rowClassName={(f) =>
                selected.has(f.id)
                  ? "bg-[var(--color-accent-muted)] hover:bg-[var(--color-accent-muted)]"
                  : undefined
              }
              {...(openFile ? { onRowClick: openFile } : {})}
              empty={
                <EmptyState
                  title="No findings"
                  description="No findings in this category with the current filters."
                  {...(filtersActive
                    ? { action: { label: "Reset filters", onClick: resetFilters } }
                    : {})}
                />
              }
            />
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
