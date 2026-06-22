"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ShieldCheck, ShieldAlert, RefreshCw, Waypoints, ListChecks, Gauge } from "lucide-react";
import { buildDsm, DsmMatrixView } from "@repowise-dev/ui/workspace/dsm";
import { AiPromptButton, AiPromptModal, buildConformanceAiPrompt } from "@repowise-dev/ui/health";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise-dev/ui/ui/card";
import { StatCard } from "@repowise-dev/ui/shared/stat-card";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import {
  useWorkspaceSystemGraph,
  useWorkspaceConformance,
  useWorkspaceArchitecture,
} from "@/lib/hooks/use-workspace";

export default function ConformancePage() {
  const router = useRouter();
  const [promptOpen, setPromptOpen] = useState(false);
  const { data: graph, isLoading: graphLoading } = useWorkspaceSystemGraph();
  const { data: report, isLoading: reportLoading } = useWorkspaceConformance();
  const { data: metrics } = useWorkspaceArchitecture();

  const isLoading = graphLoading || reportLoading;
  const matrix = useMemo(() => buildDsm(graph, report), [graph, report]);

  const violations = report?.violations ?? [];
  const cycles = report?.cycles ?? [];

  return (
    <div className="p-5 sm:p-8 space-y-6 max-w-[1400px]">
      <div>
        <div className="flex items-center gap-2.5 mb-1">
          <ShieldCheck className="h-6 w-6 text-[var(--color-accent-primary)]" />
          <h1 className="text-2xl font-semibold text-[var(--color-text-primary)]">
            Architecture Conformance
          </h1>
        </div>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Declared dependency rules checked against the live system graph, plus any
          circular service dependencies. Declare rules under{" "}
          <code className="text-[var(--color-text-primary)]">conformance:</code> in
          your <code className="text-[var(--color-text-primary)]">.repowise-workspace.yaml</code>;
          run <code className="text-[var(--color-text-primary)]">repowise workspace check</code> to
          gate CI.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="Architecture score"
          value={metrics ? `${metrics.score.toFixed(1)} / 10` : "—"}
          description={
            metrics
              ? `${metrics.architecture_type} · ${metrics.propagation_cost_pct.toFixed(1)}% propagation cost`
              : "Coupling + core roll-up"
          }
          icon={<Gauge className="h-4 w-4 text-[var(--color-accent-primary)]" />}
        />
        <StatCard
          label="Rules evaluated"
          value={isLoading ? "—" : (report?.rules_evaluated ?? 0)}
          icon={<ListChecks className="h-4 w-4 text-[var(--color-accent-secondary)]" />}
        />
        <StatCard
          label="Violations"
          value={isLoading ? "—" : violations.length}
          description="Dependencies that break a declared rule"
          icon={<ShieldAlert className="h-4 w-4 text-[var(--color-risk-high)]" />}
        />
        <StatCard
          label="Dependency cycles"
          value={isLoading ? "—" : cycles.length}
          description="Circular service dependencies"
          icon={<RefreshCw className="h-4 w-4 text-[var(--color-warning)]" />}
        />
      </div>

      {/* DSM */}
      <Card>
        <CardHeader className="pb-2 flex-row items-center justify-between">
          <CardTitle className="text-sm font-medium">
            Dependency-structure matrix
          </CardTitle>
          <button
            type="button"
            onClick={() => router.push("/workspace/system-map")}
            className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
          >
            <Waypoints className="h-3.5 w-3.5" />
            Open System Map
          </button>
        </CardHeader>
        <CardContent className="pt-0">
          <p className="text-xs text-[var(--color-text-tertiary)] mb-3">
            Each filled cell means the row service depends on the column service,
            tinted by transport. Red cells break a rule; amber cells sit on a cycle.
          </p>
          {isLoading ? (
            <Skeleton className="h-72 w-full" />
          ) : (
            <DsmMatrixView matrix={matrix} {...(metrics ? { metrics } : {})} />
          )}
        </CardContent>
      </Card>

      {/* Governance findings */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-2 flex-row items-center justify-between gap-2">
            <CardTitle className="text-sm font-medium inline-flex items-center gap-2">
              <ShieldAlert className="h-4 w-4 text-[var(--color-risk-high)]" />
              Rule violations ({violations.length})
            </CardTitle>
            {violations.length > 0 && (
              <AiPromptButton
                label="Fix violations with AI"
                onClick={() => setPromptOpen(true)}
              />
            )}
          </CardHeader>
          <CardContent className="pt-0 space-y-3">
            {isLoading ? (
              <Skeleton className="h-20 w-full" />
            ) : violations.length === 0 ? (
              <p className="text-sm text-[var(--color-text-tertiary)]">
                {report && report.rules_evaluated > 0
                  ? "No dependencies violate the declared rules."
                  : "No rules declared yet. Add a conformance block to start enforcing allowed dependencies."}
              </p>
            ) : (
              violations.map((v) => (
                <div
                  key={`${v.edge_id}:${v.rule_source}:${v.rule_target}`}
                  className="rounded-md border border-[var(--color-border-subtle)] p-3 text-sm"
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-semibold text-[var(--color-text-primary)]">
                      {v.source_name || v.source}
                    </span>
                    <span className="text-[var(--color-text-tertiary)]">→</span>
                    <span className="font-semibold text-[var(--color-text-primary)]">
                      {v.target_name || v.target}
                    </span>
                    <span className="text-xs text-[var(--color-text-tertiary)]">
                      ({v.edge_kind})
                    </span>
                  </div>
                  <div className="text-[var(--color-text-secondary)] mt-1">
                    breaks{" "}
                    <code className="text-[var(--color-warning)]">
                      {v.rule_source} !-&gt; {v.rule_target}
                    </code>
                  </div>
                  {v.rule_description && (
                    <div className="text-xs text-[var(--color-text-tertiary)] mt-1">
                      {v.rule_description}
                    </div>
                  )}
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium inline-flex items-center gap-2">
              <RefreshCw className="h-4 w-4 text-[var(--color-warning)]" />
              Dependency cycles ({cycles.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0 space-y-3">
            {isLoading ? (
              <Skeleton className="h-20 w-full" />
            ) : cycles.length === 0 ? (
              <p className="text-sm text-[var(--color-text-tertiary)]">
                No circular service dependencies detected.
              </p>
            ) : (
              cycles.map((c) => (
                <div
                  key={c.nodes.join("->")}
                  className="rounded-md border border-[var(--color-border-subtle)] p-3 text-sm"
                >
                  <div className="text-xs uppercase tracking-wide text-[var(--color-text-tertiary)] mb-1">
                    {c.length} services
                  </div>
                  <div className="text-[var(--color-text-primary)] break-words">
                    {c.nodes.join(" → ")} → {c.nodes[0]}
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      <AiPromptModal
        open={promptOpen}
        onOpenChange={setPromptOpen}
        getPrompt={(flavor) =>
          buildConformanceAiPrompt({
            violations: violations.map((v) => ({
              source: v.source,
              target: v.target,
              source_name: v.source_name,
              target_name: v.target_name,
              edge_kind: v.edge_kind,
              rule_source: v.rule_source,
              rule_target: v.rule_target,
              rule_description: v.rule_description,
            })),
            flavor,
          })
        }
        title="AI conformance fix"
        description="A ready-to-paste prompt that has your AI agent resolve these architecture rule violations by removing the disallowed dependencies."
      />
    </div>
  );
}
