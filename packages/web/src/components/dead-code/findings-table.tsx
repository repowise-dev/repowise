"use client";

import { useState, useEffect } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@repowise/ui/ui/tabs";
import { Button } from "@repowise/ui/ui/button";
import { Skeleton } from "@repowise/ui/ui/skeleton";
import { ConfirmDialog } from "@repowise/ui/ui/confirm-dialog";
import { EmptyState } from "@repowise/ui/shared/empty-state";
import { FindingRow } from "./finding-row";
import { listDeadCode, patchDeadCodeFinding } from "@/lib/api/dead-code";
import type { DeadCodeFindingResponse } from "@/lib/api/types";

type Kind = "unreachable_file" | "unused_export" | "zombie_package";

const TABS: Array<{ value: Kind; label: string }> = [
  { value: "unreachable_file", label: "Unreachable Files" },
  { value: "unused_export", label: "Unused Exports" },
  { value: "zombie_package", label: "Zombie Packages" },
];

interface FindingsTableProps {
  repoId: string;
}

export function FindingsTable({ repoId }: FindingsTableProps) {
  const [activeTab, setActiveTab] = useState<Kind>("unreachable_file");
  const [minConfidence, setMinConfidence] = useState(0.4);
  const [safeOnly, setSafeOnly] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [items, setItems] = useState<Record<Kind, DeadCodeFindingResponse[]>>({
    unreachable_file: [],
    unused_export: [],
    zombie_package: [],
  });
  const [bulkPending, setBulkPending] = useState(false);
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false);

  const { data, isLoading } = useSWR(
    `dead-code:${repoId}:${activeTab}:${minConfidence}:${safeOnly}`,
    () =>
      listDeadCode(repoId, {
        kind: activeTab,
        min_confidence: minConfidence,
        safe_only: safeOnly || undefined,
        status: "open",
        limit: 200,
      }),
    { revalidateOnFocus: false },
  );

  useEffect(() => {
    if (!data) return;
    setItems((prev) => ({ ...prev, [activeTab]: data }));
    setSelected(new Set());
  }, [data, activeTab]);

  const handleUpdate = (updated: DeadCodeFindingResponse) => {
    setItems((prev) => ({
      ...prev,
      [activeTab]: prev[activeTab].map((f) => (f.id === updated.id ? updated : f)),
    }));
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
    const current = items[activeTab];
    if (selected.size === current.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(current.map((f) => f.id)));
    }
  };

  const resolveSelected = async () => {
    const ids = Array.from(selected);
    setBulkPending(true);
    let succeeded = 0;
    try {
      for (const id of ids) {
        try {
          const updated = await patchDeadCodeFinding(id, { status: "resolved" });
          handleUpdate(updated);
          succeeded += 1;
        } catch {
          // continue; we'll report partial below
        }
      }
      setSelected(new Set());
      setBulkConfirmOpen(false);
      if (succeeded === ids.length) {
        toast.success(`Resolved ${succeeded} finding${succeeded === 1 ? "" : "s"}`);
      } else if (succeeded > 0) {
        toast.warning(`Resolved ${succeeded} of ${ids.length}; some failed`);
      } else {
        toast.error("Couldn't resolve findings");
      }
    } finally {
      setBulkPending(false);
    }
  };

  const current = items[activeTab];

  return (
    <div className="space-y-4">
      {/* Controls */}
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
          Safe to delete only
        </label>

        {selected.size > 0 && (
          <Button
            size="sm"
            variant="outline"
            disabled={bulkPending}
            onClick={() => setBulkConfirmOpen(true)}
            className="text-green-500 border-green-500/30 hover:bg-green-500/10"
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

      <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as Kind)}>
        <TabsList>
          {TABS.map((t) => (
            <TabsTrigger key={t.value} value={t.value}>
              {t.label}
              {items[t.value].length > 0 && (
                <span className="ml-1.5 text-xs text-[var(--color-text-tertiary)]">
                  {items[t.value].length}
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
                        selected={selected.has(f.id)}
                        onToggle={toggleSelect}
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
