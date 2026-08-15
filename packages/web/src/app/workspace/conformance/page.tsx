import type { Metadata } from "next";
import Link from "next/link";
import { ShieldCheck } from "lucide-react";
import type {
  ConformanceReport,
  ArchitectureMetrics,
} from "@repowise-dev/api-client/types";
import { PageShell } from "@repowise-dev/ui/shared";
import { PageLede, LedeLink } from "@repowise-dev/ui/shared/page-lede";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { OverviewSection, SectionLink } from "@repowise-dev/ui/overview";
import { StatRibbon, type RibbonStat } from "@repowise-dev/ui/stats/stat-ribbon";
import { buildDsm, DsmMatrixView } from "@repowise-dev/ui/workspace/dsm";
import { formatNumber } from "@repowise-dev/ui/lib/format";
import {
  getWorkspaceSystemGraph,
  getWorkspaceConformance,
  getWorkspaceArchitecture,
} from "@/lib/api/workspace";
import { ConformanceAiPrompt } from "./conformance-ai-prompt";

export const metadata: Metadata = { title: "Conformance" };

export const revalidate = 30;

/**
 * The matrix counts a co-change relationship as a filled cell and the ribbon's
 * structural-link figure does not, so the two numbers differ by design and sit
 * a few hundred pixels apart. Reconciled in the copy rather than left for the
 * reader to work out, and stated here rather than changing the shared matrix's
 * own caption, which is accurate for what it counts.
 */
const DSM_DESCRIPTION =
  "Each filled cell means the row service depends on the column service, tinted by transport. Red cells break a declared rule; amber cells sit on a cycle. The matrix counts co-change relationships too, so its total runs above the structural-link figure above.";

/**
 * Whether the conformance analyser has ever produced this report.
 *
 * A report with no `generated_at` was never written a result, and its zero
 * counts are the absence of a check rather than a clean pass. Three states
 * have to stay separate: never ran, ran with no rules to check, and ran
 * against real rules.
 *
 * `generated_at` is now `string | null`, and the checker stamps it whenever it
 * runs. Artifacts written before that carry `""`, which the loader maps to
 * null, so the falsy check covers both.
 */
function reportState(
  report: ConformanceReport | null,
): "unavailable" | "never_ran" | "no_rules" | "checked" {
  if (!report) return "unavailable";
  if (!report.generated_at) return "never_ran";
  if ((report.rules_evaluated ?? 0) === 0) return "no_rules";
  return "checked";
}

export default async function ConformancePage() {
  const [sg, cf, arch] = await Promise.allSettled([
    getWorkspaceSystemGraph(),
    getWorkspaceConformance(),
    getWorkspaceArchitecture(),
  ]);

  const graph = sg.status === "fulfilled" ? sg.value : null;
  const report = cf.status === "fulfilled" ? cf.value : null;
  const metrics = arch.status === "fulfilled" ? arch.value : null;

  const state = reportState(report);
  const violations = state === "checked" ? (report?.violations ?? []) : [];
  // Cycle *membership* comes from the report, but the count is recomputed from
  // the graph on every request, so it stays true even when nothing has run.
  const cycles = report?.cycles ?? [];
  const cycleCount = metrics?.cycle_count ?? cycles.length;

  const matrix = buildDsm(graph, report);

  const ribbon: RibbonStat[] = [
    {
      label: "Services",
      value: metrics ? formatNumber(metrics.node_count) : "—",
      sub: "repositories and their sub-packages",
    },
    {
      label: "Structural links",
      value: metrics ? formatNumber(metrics.structural_edge_count) : "—",
      sub: "contracts and imports, not co-changes",
    },
    {
      label: "Propagation cost",
      value: metrics ? `${metrics.propagation_cost_pct.toFixed(1)}%` : "—",
      sub: "of the system a change can reach",
    },
    {
      label: "Core size",
      value: metrics ? formatNumber(metrics.core_size) : "—",
      sub: metrics
        ? `${Math.round(metrics.core_ratio * 100)}% of all services`
        : "mutually dependent centre",
    },
    {
      label: "Cycles",
      value: metrics ? formatNumber(cycleCount) : "—",
      sub: "circular service dependencies",
    },
  ];

  return (
    <PageShell
      title="Conformance"
      icon={<ShieldCheck className="h-5 w-5 text-[var(--color-text-tertiary)]" />}
      description="How the workspace is shaped, and whether it obeys the dependency rules you declared."
    >
      <PageLede
        label="Architecture score"
        value={metrics ? metrics.score.toFixed(1) : "—"}
        unit="out of 10"
        {...(metrics?.architecture_type
          ? { band: { label: metrics.architecture_type } }
          : {})}
        layout="beside"
        action={
          <LedeLink href="/workspace/system-map" LinkComponent={Link}>
            See the system map
          </LedeLink>
        }
      >
        {metrics ? (
          <p>
            A change here can reach {metrics.propagation_cost_pct.toFixed(1)}% of the system
            through {formatNumber(metrics.structural_edge_count)} structural links between{" "}
            {formatNumber(metrics.node_count)} services. The score weighs that reach against
            the size of the mutually dependent core and any cycles; it is computed from the
            system graph on every request, so it does not depend on the check below having run.
          </p>
        ) : (
          <p>
            No system graph has been built yet, so there is nothing to measure. Run a workspace
            sync to build one.
          </p>
        )}
        {ruleSentence(state, report)}
      </PageLede>

      <StatRibbon stats={ribbon} />

      <OverviewSection
        title="Dependency-structure matrix"
        description={DSM_DESCRIPTION}
        action={
          <SectionLink href="/workspace/system-map" LinkComponent={Link}>
            System map
          </SectionLink>
        }
      >
        <DsmMatrixView matrix={matrix} {...(metrics ? { metrics } : {})} />
      </OverviewSection>

      <OverviewSection
        title={
          state === "checked" ? `Rule violations (${violations.length})` : "Rule violations"
        }
        description="Dependencies that exist in the graph but are forbidden by a rule you declared."
        {...(violations.length > 0
          ? { action: <ConformanceAiPrompt violations={violations} /> }
          : {})}
      >
        {violations.length === 0 ? (
          <ViolationsEmptyState state={state} />
        ) : (
          <ul className="m-0 list-none divide-y divide-[var(--color-border-default)] border-t border-[var(--color-border-default)] p-0">
            {violations.map((v) => (
              <li
                key={`${v.edge_id}:${v.rule_source}:${v.rule_target}`}
                className="py-3.5"
              >
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                  <span className="font-medium text-[var(--color-text-primary)]">
                    {v.source_name || v.source}
                  </span>
                  <span aria-hidden className="text-[var(--color-text-tertiary)]">
                    →
                  </span>
                  <span className="font-medium text-[var(--color-text-primary)]">
                    {v.target_name || v.target}
                  </span>
                  <span className="font-mono text-[11px] text-[var(--color-text-tertiary)]">
                    {v.edge_kind}
                  </span>
                </div>
                <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                  Breaks{" "}
                  <span className="font-mono text-[var(--color-warning)]">
                    {v.rule_source} !&gt; {v.rule_target}
                  </span>
                  {v.rule_description ? ` — ${v.rule_description}` : ""}
                </p>
              </li>
            ))}
          </ul>
        )}
      </OverviewSection>

      <OverviewSection
        title={`Dependency cycles (${cycleCount})`}
        description="Groups of services that depend on each other in a loop, so none of them can be changed or deployed independently."
      >
        {cycles.length === 0 ? (
          <EmptyState
            className="p-6"
            title={
              cycleCount > 0
                ? `${cycleCount} ${cycleCount === 1 ? "cycle" : "cycles"} detected, but not listed`
                : "No circular dependencies"
            }
            description={
              cycleCount > 0
                ? "The count is recomputed from the system graph, but the services in each cycle are recorded by the conformance check. Run it to see which services are involved."
                : "Every dependency in the graph runs one way. Nothing has to be changed in lockstep."
            }
          />
        ) : (
          <ul className="m-0 list-none divide-y divide-[var(--color-border-default)] border-t border-[var(--color-border-default)] p-0">
            {cycles.map((c) => (
              <li key={c.nodes.join("->")} className="py-3.5">
                <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                  {c.length} services
                </p>
                <p className="mt-1 text-sm text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
                  {c.nodes.join(" → ")} → {c.nodes[0]}
                </p>
              </li>
            ))}
          </ul>
        )}
      </OverviewSection>
    </PageShell>
  );
}

/** The sentence in the lede that says whether the check has anything to say. */
function ruleSentence(
  state: ReturnType<typeof reportState>,
  report: ConformanceReport | null,
) {
  if (state === "checked") {
    const n = report?.rules_evaluated ?? 0;
    const v = report?.violations?.length ?? 0;
    return (
      <p>
        {formatNumber(n)} declared {n === 1 ? "rule was" : "rules were"} checked against that
        graph, and {v === 0 ? "nothing breaks them" : `${formatNumber(v)} ${v === 1 ? "dependency breaks" : "dependencies break"} them`}.
      </p>
    );
  }
  if (state === "no_rules") {
    return (
      <p>
        The check has run, but no dependency rules are declared, so it had nothing to enforce.
        Add a <code className="font-mono text-[var(--color-text-primary)]">conformance:</code>{" "}
        block to{" "}
        <code className="font-mono text-[var(--color-text-primary)]">
          .repowise-workspace.yaml
        </code>{" "}
        to describe which services may depend on which.
      </p>
    );
  }
  return (
    <p>
      The conformance check has not run on this workspace yet, so nothing below is a verdict —
      an empty violations list here means unmeasured, not clean. Run{" "}
      <code className="font-mono text-[var(--color-text-primary)]">
        repowise workspace check
      </code>{" "}
      to evaluate it.
    </p>
  );
}

/**
 * Three empty states, because they mean three different things.
 *
 * Collapsing them into one "no violations" message is the failure this page
 * shipped with: the artifact's zeros are written before anything runs.
 */
function ViolationsEmptyState({ state }: { state: ReturnType<typeof reportState> }) {
  if (state === "checked") {
    return (
      <EmptyState
        className="p-6"
        title="No rules are broken"
        description="Every dependency in the graph is allowed by the rules you declared."
      />
    );
  }
  if (state === "no_rules") {
    return (
      <EmptyState
        className="p-6"
        title="No rules declared"
        description="Nothing has been forbidden yet, so nothing can be violated. Declare which services may depend on which under `conformance:` in .repowise-workspace.yaml, then run `repowise workspace check` to gate CI on it."
      />
    );
  }
  if (state === "never_ran") {
    return (
      <EmptyState
        className="p-6"
        title="Not checked yet"
        description="The conformance analyser has not run on this workspace, so no rules have been evaluated. This is not a clean bill of health — run `repowise workspace check` to produce one."
      />
    );
  }
  return (
    <EmptyState
      className="p-6"
      title="Conformance report unavailable"
      description="The report could not be read. Run a workspace sync to rebuild it."
    />
  );
}
