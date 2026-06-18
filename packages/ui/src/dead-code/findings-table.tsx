"use client";

import { useMemo, useState } from "react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../ui/tabs";
import { Button } from "../ui/button";
import { Skeleton } from "../ui/skeleton";
import { ConfirmDialog } from "../ui/confirm-dialog";
import { EmptyState } from "../shared/empty-state";
import { FindingRow } from "./finding-row";
import type { DeadCodeFinding, DeadCodeStatus } from "@repowise-dev/types/dead-code";

type Kind = "unreachable_file" | "unused_export" | "zombie_package";

const TABS: Array<{ value: Kind; label: string }> = [
  { value: "unreachable_file", label: "Unreachable Files" },
  { value: "unused_export", label: "Unused Exports" },
  { value: "zombie_package", label: "Zombie Packages" },
];

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
  const [activeTab, setActiveTab] = useState<Kind>("unreachable_file");
  const [minConfidence, setMinConfidence] = useState(0.4);
  const [safeOnly, setSafeOnly] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [overrides, setOverrides] = useState<Record<string, DeadCodeFinding>>({});
  const [bulkPending, setBulkPending] = useState(false);
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false);

  // Apply local optimistic overrides over the host-supplied slice so a resolved
  // row updates without a refetch.
  const merged = useMemo(
    () => findings.map((f) => overrides[f.id] ?? f),
    [findings, overrides],
  );

  const byKind = useMemo(() => {
    const buckets: Record<Kind, DeadCodeFinding[]> = {
      unreachable_file: [],
      unused_export: [],
      zombie_package: [],
    };
    for (const f of merged) {
      if (f.status !== "open") continue;
      if (f.confidence < minConfidence) continue;
      if (safeOnly && !f.safe_to_delete) continue;
      const kind = f.kind as Kind;
      if (kind in buckets) buckets[kind].push(f);
    }
    return buckets;
  }, [merged, minConfidence, safeOnly]);

  const current = byKind[activeTab];

  const handleUpdate = (updated: DeadCodeFinding) => {
    setOverrides((prev) => ({ ...prev, [updated.id]: updated }));
  };

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selected.size === current.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(current.map((f) => f.id)));
    }
  };

  const resolveSelected = async () => {
    if (!onBulkResolve) return;
    const ids = Array.from(selected);
    setBulkPending(true);
    try {
      const succeededIds = await onBulkResolve(ids);
      // Reflect only the rows that actually resolved — never a positional guess.
      const resolvedIds = new Set(succeededIds);
      setOverrides((prev) => {
        const next = { ...prev };
        for (const f of merged) {
          if (resolvedIds.has(f.id)) next[f.id] = { ...f, status: "resolved" as DeadCodeStatus };
        }
        return next;
      });
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
          <input
            id="min-confidence"
            type="range"
            min="0.4"
            max="1"
            step="0.05"
            value={minConfidence}
            onChange={(e) => setMinConfidence(Number(e.target.value))}
            aria-valuetext={`${Math.round(minConfidence * 100)} percent`}
            className="w-28 accent-[var(--color-accent-primary)]"
          />
        </div>
        <label className="flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)] cursor-pointer select-none">
          <input
            type="checkbox"
            checked={safeOnly}
            onChange={(e) => setSafeOnly(e.target.checked)}
            className="rounded border-[var(--color-border-default)]"
          />
          Cleanup-ready only
        </label>

        {onBulkResolve && selected.size > 0 && (
          <Button
            size="sm"
            variant="outline"
            disabled={bulkPending}
            onClick={() => setBulkConfirmOpen(true)}
            className="text-[var(--color-success)] border-[var(--color-success)]/30 hover:bg-[var(--color-success)]/10"
          >
            {bulkPending ? "Resolving…" : `Resolve ${selected.size} selected`}
          </Button>
        )}
      </div>
      <ConfirmDialog
        open={bulkConfirmOpen}
        onOpenChange={setBulkConfirmOpen}
        title={`Resolve ${selected.size} finding${selected.size === 1 ? "" : "s"}?`}
        description="This will mark each selected finding as resolved."
        confirmLabel="Resolve all"
        loading={bulkPending}
        onConfirm={resolveSelected}
      />

      <Tabs
        value={activeTab}
        onValueChange={(v) => {
          setActiveTab(v as Kind);
          // Selection is per-kind; clearing on tab change keeps "Resolve N
          // selected" and the select-all checkbox scoped to the visible rows.
          setSelected(new Set());
        }}
      >
        <TabsList>
          {TABS.map((t) => (
            <TabsTrigger key={t.value} value={t.value}>
              {t.label}
              {byKind[t.value].length > 0 && (
                <span className="ml-1.5 text-xs text-[var(--color-text-tertiary)]">
                  {byKind[t.value].length}
                </span>
              )}
            </TabsTrigger>
          ))}
        </TabsList>

        {TABS.map((t) => (
          <TabsContent key={t.value} value={t.value}>
            {isLoading && current.length === 0 ? (
              <div className="space-y-2 mt-2">
                {Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="h-10 w-full" />
                ))}
              </div>
            ) : current.length === 0 ? (
              <EmptyState
                title="No findings"
                description="No open findings for this category with current filters."
              />
            ) : (
              <div className="rounded-lg border border-[var(--color-border-default)] overflow-x-auto mt-2">
                <table className="w-full text-sm">
                  <caption className="sr-only">Dead code findings</caption>
                  <thead className="sticky top-0 z-10 bg-[var(--color-bg-elevated)]">
                    <tr className="border-b border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]">
                      <th scope="col" className="px-4 py-2.5 w-8">
                        <input
                          type="checkbox"
                          checked={selected.size === current.length && current.length > 0}
                          onChange={toggleSelectAll}
                          aria-label="Select all findings"
                          className="rounded border-[var(--color-border-default)]"
                        />
                      </th>
                      <th scope="col" className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
                        File / Symbol
                      </th>
                      <th scope="col" className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-24">
                        Confidence
                      </th>
                      <th scope="col" className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider hidden md:table-cell">
                        Owner
                      </th>
                      <th scope="col" className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider w-16 hidden md:table-cell">
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
                        onUpdate={handleUpdate}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}
