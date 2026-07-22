"use client";

import { useMemo, useState } from "react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../ui/tabs";
import { Button } from "../ui/button";
import { Slider } from "../ui/slider";
import { Switch } from "../ui/switch";
import { TableSkeleton } from "../shared/loading-skeletons";
import { ConfirmDialog } from "../ui/confirm-dialog";
import { EmptyState } from "../shared/empty-state";
import { FindingRow } from "./finding-row";
import type { DeadCodeFinding, DeadCodeStatus } from "@repowise-dev/types/dead-code";

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

export interface FindingsTableProps {
  /** Full open-findings slice; the table filters by kind / confidence / safety client-side. */
  findings: DeadCodeFinding[];
  /** Repo base path used to build row action links. */
  repoId: string;
  /** Injected mutation — host owns the API call, optimistic toast + undo. */
  onPatch: (id: string, patch: { status: DeadCodeStatus }) => Promise<DeadCodeFinding>;
  /** Injected bulk resolve — host owns the toast/partial-failure messaging. Returns the ids that actually resolved. */
  onBulkResolve?: (ids: string[]) => Promise<string[]>;
  isLoading?: boolean;
}

/**
 * Canonical interactive dead-code table: kind tabs, a confidence slider (the
 * single confidence control for the spine), cleanup-ready filter, bulk resolve,
 * and per-row status actions. Pure presentation — the host injects the data
 * slice and the patch/bulk mutations, so this propagates via the package.
 */
export function FindingsTable({ findings, repoId, onPatch, onBulkResolve, isLoading }: FindingsTableProps) {
  const [activeTab, setActiveTab] = useState<string | null>(null);
  const [minConfidence, setMinConfidence] = useState(0.4);
  const [safeOnly, setSafeOnly] = useState(false);
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
    const buckets: Record<string, DeadCodeFinding[]> = {};
    for (const t of tabs) buckets[t.value] = [];
    for (const f of findings) {
      if (f.confidence < minConfidence) continue;
      if (safeOnly && !f.safe_to_delete) continue;
      buckets[f.kind]?.push(f);
    }
    return buckets;
  }, [findings, tabs, minConfidence, safeOnly]);

  // The active tab can disappear when the slice changes (a refetch, a resolved
  // last row); fall back to the first tab rather than rendering nothing.
  const effectiveTab =
    activeTab && tabs.some((t) => t.value === activeTab) ? activeTab : (tabs[0]?.value ?? null);
  const current = useMemo(
    () => (effectiveTab ? (byKind[effectiveTab] ?? []) : []),
    [byKind, effectiveTab],
  );

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

  const resolveSelected = async () => {
    if (!onBulkResolve) return;
    setBulkPending(true);
    try {
      // Send the visible intersection, which is what the confirm dialog counted.
      await onBulkResolve(visibleSelected);
      setSelected(new Set());
      setBulkConfirmOpen(false);
    } finally {
      setBulkPending(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Controls — the confidence slider is the only confidence axis. */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2">
          <label htmlFor="min-confidence" className="text-xs text-[var(--color-text-secondary)]">
            Min confidence: {Math.round(minConfidence * 100)}%
          </label>
          <Slider
            id="min-confidence"
            min={0.4}
            max={1}
            step={0.05}
            value={[minConfidence]}
            onValueChange={([v]) => setMinConfidence(v ?? 0.4)}
            aria-label="Minimum confidence"
            className="w-28"
          />
        </div>
        <label className="flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)] cursor-pointer select-none">
          <Switch checked={safeOnly} onCheckedChange={setSafeOnly} />
          Cleanup-ready only
        </label>

        {onBulkResolve && selectedCount > 0 && (
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
          description="No open dead-code findings for this repository."
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

        {tabs.map((t) => (
          <TabsContent key={t.value} value={t.value}>
            {current.length === 0 ? (
              <EmptyState
                title="No findings"
                description="No open findings for this category with current filters."
              />
            ) : (
              <div className="border border-[var(--color-border-default)] overflow-x-auto overflow-hidden mt-2">
                <table className="w-full text-sm">
                  <caption className="sr-only">Dead code findings</caption>
                  <thead className="sticky top-0 z-10 bg-[var(--color-bg-surface)]">
                    <tr className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-surface)]">
                      <th scope="col" className="px-4 py-2.5 w-8">
                        <input
                          type="checkbox"
                          checked={allVisibleSelected}
                          onChange={toggleSelectAll}
                          aria-label="Select all findings"
                          className="rounded border-[var(--color-border-default)]"
                        />
                      </th>
                      <th scope="col" className="px-4 py-2.5 text-left text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                        File / Symbol
                      </th>
                      <th scope="col" className="px-4 py-2.5 text-left text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-24">
                        Confidence
                      </th>
                      <th scope="col" className="px-4 py-2.5 text-left text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider hidden md:table-cell">
                        Owner
                      </th>
                      <th scope="col" className="px-4 py-2.5 text-left text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-16 hidden md:table-cell">
                        Lines
                      </th>
                      <th scope="col" className="px-4 py-2.5 w-20 hidden sm:table-cell">
                        <span className="sr-only">Safety</span>
                      </th>
                      <th scope="col" className="px-4 py-2.5 w-24 hidden sm:table-cell">
                        <span className="sr-only">Status</span>
                      </th>
                      <th scope="col" className="px-4 py-2.5 w-36">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {current.map((f) => (
                      <FindingRow
                        key={f.id}
                        finding={f}
                        repoId={repoId}
                        selected={selected.has(f.id)}
                        onToggle={toggleSelect}
                        onPatch={onPatch}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </TabsContent>
        ))}
      </Tabs>
      )}
    </div>
  );
}
