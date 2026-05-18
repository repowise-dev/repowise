"use client";

import { useMemo, useState } from "react";
import { Wrench } from "lucide-react";
import { useParams } from "next/navigation";
import useSWR from "swr";

import {
  AiPromptModal,
  ImpactEffortQuadrant,
  RefactoringTargetList,
  biomarkerLabel,
  buildAiPrompt,
} from "@repowise-dev/ui/health";
import type { FindingStatus, RefactoringTarget } from "@repowise-dev/ui/health";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

import {
  getHealthOverview,
  getRefactoringTargets,
  updateFindingStatus,
  type HealthOverviewResponse,
  type RefactoringTargetsResponse,
  type RefactoringQuery,
} from "@/lib/api/code-health";
import { HealthPageChrome } from "@/components/health/health-page-chrome";
import { HealthFileDrawerHost } from "@/components/health/health-file-drawer-host";

type GroupBy = "none" | "biomarker" | "module" | "effort";

const EFFORT_LABEL: Record<string, string> = {
  S: "Small (≤40 NLOC)",
  M: "Medium (≤150 NLOC)",
  L: "Large (≤400 NLOC)",
  XL: "Extra large (>400 NLOC)",
};

export default function RefactoringTargetsPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const [biomarker, setBiomarker] = useState<string | "all">("all");
  const [minSeverity, setMinSeverity] = useState<string>("all");
  const [maxEffort, setMaxEffort] = useState<string>("all");
  const [sort, setSort] = useState<RefactoringQuery["sort"]>("impact_per_effort");
  const [groupBy, setGroupBy] = useState<GroupBy>("none");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [promptTarget, setPromptTarget] = useState<RefactoringTarget | null>(null);

  const queryKey = useMemo(
    () => JSON.stringify({ id, biomarker, minSeverity, maxEffort, sort }),
    [id, biomarker, minSeverity, maxEffort, sort],
  );

  const { data, isLoading, error, mutate } = useSWR<RefactoringTargetsResponse>(
    `code-health-refactoring:${queryKey}`,
    () =>
      getRefactoringTargets(id, {
        limit: 200,
        biomarker: biomarker === "all" ? undefined : biomarker,
        min_severity: minSeverity === "all" ? undefined : minSeverity,
        max_effort: maxEffort === "all" ? undefined : maxEffort,
        sort,
      }),
    { revalidateOnFocus: false },
  );

  const { data: overview } = useSWR<HealthOverviewResponse>(
    `code-health-overview:${id}`,
    () => getHealthOverview(id, 25),
    { revalidateOnFocus: false },
  );

  const handleStatus = async (findingId: string, status: FindingStatus) => {
    await updateFindingStatus(id, findingId, status);
    mutate();
  };

  const biomarkerOptions = useMemo(() => {
    const set = new Set<string>();
    (overview?.biomarkers ?? []).forEach((b) => set.add(b.biomarker_type));
    (data?.targets ?? []).forEach((t) => t.biomarkers.forEach((b) => set.add(b)));
    return [...set].sort();
  }, [overview, data]);

  const grouped = useMemo(() => {
    if (!data) return [];
    const targets = data.targets;
    if (groupBy === "none") return [{ key: "All", targets }];
    const groups = new Map<string, typeof targets>();
    for (const t of targets) {
      let key = "—";
      if (groupBy === "biomarker") key = biomarkerLabel(t.primary_biomarker);
      else if (groupBy === "module") key = t.module ?? "(no module)";
      else if (groupBy === "effort") key = EFFORT_LABEL[t.effort_bucket] ?? t.effort_bucket;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(t);
    }
    return [...groups.entries()]
      .sort((a, b) => b[1].length - a[1].length)
      .map(([key, targets]) => ({ key, targets }));
  }, [data, groupBy]);

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1400px]">
      <HealthPageChrome
        repoId={id}
        active="refactoring"
        title="Refactoring targets"
        icon={<Wrench className="h-5 w-5 text-orange-500" />}
        subtitle="Files ranked by total health impact divided by an effort proxy (NLOC bucket). High leverage = high impact, low effort."
        meta={overview?.meta}
      />

      <div className="flex flex-wrap items-center gap-2">
        <Select
          label="Biomarker"
          value={biomarker}
          onChange={(v) => setBiomarker(v)}
          options={[
            { value: "all", label: "All biomarkers" },
            ...biomarkerOptions.map((b) => ({ value: b, label: biomarkerLabel(b) })),
          ]}
        />
        <Select
          label="Min severity"
          value={minSeverity}
          onChange={setMinSeverity}
          options={[
            { value: "all", label: "Any severity" },
            { value: "low", label: "Low+" },
            { value: "medium", label: "Medium+" },
            { value: "high", label: "High+" },
            { value: "critical", label: "Critical only" },
          ]}
        />
        <Select
          label="Max effort"
          value={maxEffort}
          onChange={setMaxEffort}
          options={[
            { value: "all", label: "Any effort" },
            { value: "S", label: "Small only" },
            { value: "M", label: "Medium+" },
            { value: "L", label: "Large+" },
          ]}
        />
        <Select
          label="Sort"
          value={sort ?? "impact_per_effort"}
          onChange={(v) => setSort(v as RefactoringQuery["sort"])}
          options={[
            { value: "impact_per_effort", label: "Leverage (impact ÷ effort)" },
            { value: "total_impact", label: "Total impact" },
            { value: "score", label: "Worst score" },
            { value: "finding_count", label: "Finding count" },
          ]}
        />
        <Select
          label="Group"
          value={groupBy}
          onChange={(v) => setGroupBy(v as GroupBy)}
          options={[
            { value: "none", label: "Flat list" },
            { value: "biomarker", label: "By biomarker" },
            { value: "module", label: "By module" },
            { value: "effort", label: "By effort" },
          ]}
        />
      </div>

      {isLoading ? (
        <div className="grid gap-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      ) : error ? (
        <p className="text-sm text-red-500">Failed to load refactoring targets.</p>
      ) : !data || data.targets.length === 0 ? (
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-6 text-sm text-[var(--color-text-secondary)]">
          No refactoring targets match the current filters. Try widening the
          severity or effort filters, or run{" "}
          <code className="px-1 rounded bg-[var(--color-bg-muted)]">repowise health</code>{" "}
          to populate findings.
        </div>
      ) : (
        <>
          <ImpactEffortQuadrant
            points={data.targets.map((t) => ({
              file_path: t.file_path,
              total_impact: t.total_impact,
              effort_bucket: t.effort_bucket,
              nloc: t.nloc,
              score: t.score,
            }))}
            onSelect={(p) => setSelectedFile(p.file_path)}
          />

          <p className="text-xs text-[var(--color-text-tertiary)]">
            Showing {data.targets.length} of {data.total} candidates.
          </p>

          <div className="space-y-6">
            {grouped.map((g) => (
              <section key={g.key} className="space-y-2">
                {groupBy !== "none" ? (
                  <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                    {g.key} <span className="text-[var(--color-text-secondary)]">({g.targets.length})</span>
                  </h3>
                ) : null}
                <RefactoringTargetList
                  targets={g.targets}
                  onSelect={(t) => setSelectedFile(t.file_path)}
                  onStatusChange={handleStatus}
                  onGeneratePrompt={(t) => setPromptTarget(t)}
                />
              </section>
            ))}
          </div>
        </>
      )}

      <HealthFileDrawerHost
        repoId={id}
        filePath={selectedFile}
        onClose={() => setSelectedFile(null)}
      />

      <AiPromptModal
        open={promptTarget !== null}
        onOpenChange={(open) => {
          if (!open) setPromptTarget(null);
        }}
        filePath={promptTarget?.file_path}
        title="AI fix prompt"
        description="A ready-to-paste prompt that gives your AI coding agent every biomarker, line range, score deduction, and constraint needed to refactor this file in one focused pass."
        getPrompt={
          promptTarget
            ? (flavor) => buildAiPrompt({ target: promptTarget, flavor })
            : null
        }
      />
    </div>
  );
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)]">
      <span className="uppercase tracking-wider">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="text-xs px-2 py-1 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] text-[var(--color-text-primary)]"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
