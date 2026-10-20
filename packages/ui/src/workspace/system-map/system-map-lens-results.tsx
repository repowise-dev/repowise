"use client";

/**
 * What the active lens found, as rows under the canvas. The canvas keeps the
 * width and carries the overlay; the list carries the names, figures and
 * actions, and every row selects its service on the map.
 */

import { useState } from "react";
import type {
  ArchitectureMetrics,
  BreakingChangeReport,
  ConformanceReport,
  CrossRepoBlastRadius,
  ImpactedNode,
  NodeArchitectureRole,
  SystemGraph,
} from "@repowise-dev/types/workspace";
import { OverviewSection } from "../../overview/section";
import { ResponsiveTable, type ResponsiveColumn } from "../../shared/responsive-table";
import { AiPromptButton } from "../../health/ai-prompt-button";
import { AiPromptModal } from "../../health/ai-prompt-modal";
import { buildConformanceAiPrompt } from "../../health/ai-prompt-builder";
import {
  BreakingChangeRow,
  breakingChangeKey,
  breakingChangeSummary,
  sortChangesBySeverity,
} from "../breaking-change-row";
import { ROLE_ORDER, roleStyle } from "./architecture";
import { edgeKindStyle } from "./edge-kinds";
import { cyclePrompt, type SystemMapPromptState } from "./system-map-drawer";
import { buildBlastRadiusAiPrompt } from "./system-map-ai-prompt";
import type { SystemMapLens } from "./system-map-lens";
import { plural } from "./system-map-model";
import type { SystemMapSelection } from "./types";

export interface SystemMapLensResultsProps {
  lens: SystemMapLens;
  graph: SystemGraph;
  selection: SystemMapSelection;
  onSelect: (selection: SystemMapSelection) => void;
  blastTarget: string | null;
  blast: CrossRepoBlastRadius | null;
  blastLoading?: boolean;
  /** The blast-radius request failed. */
  blastError?: boolean;
  includeBehavioral: boolean;
  breaking: BreakingChangeReport | null;
  conformance: ConformanceReport | null;
  architecture: ArchitectureMetrics | null;
  conformanceHref?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
}

function Muted({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-[var(--color-text-tertiary)]">{children}</p>;
}

export function SystemMapLensResults(props: SystemMapLensResultsProps) {
  const [prompt, setPrompt] = useState<SystemMapPromptState | null>(null);
  if (props.lens === "none") return null;

  return (
    <>
      {props.lens === "blast" && <BlastResults {...props} onPrompt={setPrompt} />}
      {props.lens === "breaking" && <BreakingResults {...props} />}
      {props.lens === "conformance" && <ConformanceResults {...props} onPrompt={setPrompt} />}
      {props.lens === "core" && <CoreResults {...props} />}
      <AiPromptModal
        open={prompt !== null}
        onOpenChange={(o) => !o && setPrompt(null)}
        getPrompt={prompt?.build ?? null}
        {...(prompt ? { title: prompt.title, description: prompt.description } : {})}
      />
    </>
  );
}

type WithPrompt = SystemMapLensResultsProps & { onPrompt: (p: SystemMapPromptState) => void };

// ---------------------------------------------------------------------------
// Blast radius
// ---------------------------------------------------------------------------

function BlastResults({ graph, blastTarget, blast, blastLoading, blastError, includeBehavioral, selection, onSelect, onPrompt }: WithPrompt) {
  const name = (id: string) => graph.nodes.find((n) => n.id === id)?.name ?? id;
  const targetName = blastTarget ? name(blastTarget) : null;

  if (!blastTarget) {
    return (
      <OverviewSection title="Blast radius">
        <Muted>Choose the service a change starts in, from the lens bar or with Show blast radius in its drawer.</Muted>
      </OverviewSection>
    );
  }
  if (!blast) {
    return (
      <OverviewSection title={`If ${targetName} changes`}>
        <Muted>
          {blastError
            ? "The trace failed. The service may no longer be in the graph, or the server could not be reached."
            : blastLoading
              ? "Tracing what depends on it…"
              : "No result for this service."}
        </Muted>
      </OverviewSection>
    );
  }

  const structural = blast.impacted.filter((n) => n.structural).length;
  const behavioral = blast.impacted.length - structural;
  const repos = blast.impacted_repos.length;
  const description =
    blast.impacted.length === 0
      ? `Nothing in the workspace depends on ${targetName}${includeBehavioral ? ", and nothing changes alongside it" : ""}.`
      : `${plural(blast.impacted.length, "service", "services")} in ${plural(repos, "other repository", "other repositories")} can feel it: ${structural} through a contract or import (will break), ${behavioral} only through co-change (may drift). Impact runs 0 to 1 and falls with each hop.${includeBehavioral ? "" : " Co-change is excluded."}`;

  const columns: ResponsiveColumn<ImpactedNode>[] = [
    {
      key: "name",
      header: "Service",
      priority: 1,
      render: (n) => (
        <span className="flex flex-col">
          <span className="text-[15px] font-medium text-[var(--color-text-primary)]">{n.name}</span>
          <span className="font-mono text-[10px] text-[var(--color-text-tertiary)]">
            {n.id.includes("::") ? n.id.replace("::", " / ") : `${n.repo} repository`}
          </span>
        </span>
      ),
    },
    {
      key: "nature",
      header: "Reached through",
      priority: 1,
      render: (n) =>
        n.structural ? (
          <span className="text-[var(--color-text-primary)]">Contract or import</span>
        ) : (
          <span className="text-[var(--color-text-secondary)]">Co-change only</span>
        ),
    },
    {
      key: "distance",
      header: "Distance",
      align: "right",
      priority: 2,
      render: (n) => <span className="tabular-nums">{plural(n.distance, "hop", "hops")}</span>,
    },
    {
      key: "via",
      header: "Via",
      priority: 3,
      render: (n) => (
        <span className="text-xs text-[var(--color-text-secondary)]">
          {n.edge_kinds.map((k) => edgeKindStyle(k).label).join(", ")}
        </span>
      ),
    },
    {
      key: "score",
      header: "Impact",
      align: "right",
      priority: 1,
      render: (n) => <span className="font-mono tabular-nums">{n.score.toFixed(2)}</span>,
    },
  ];

  return (
    <OverviewSection
      title={`If ${targetName} changes`}
      description={description}
      action={
        blast.impacted.length > 0 ? (
          <AiPromptButton
            label="Plan this change"
            onClick={() =>
              onPrompt({
                title: "Plan a change across the blast radius",
                description: `A ready-to-paste prompt that has your agent find the contracts and tests each impacted service needs before ${targetName} changes.`,
                build: (flavor) =>
                  buildBlastRadiusAiPrompt({
                    target: { id: blastTarget, name: targetName ?? blastTarget },
                    impacted: blast.impacted,
                    includeBehavioral,
                    flavor,
                  }),
              })
            }
          />
        ) : undefined
      }
    >
      {blast.impacted.length > 0 && (
        <ResponsiveTable
          columns={columns}
          rows={blast.impacted}
          rowKey={(n) => n.id}
          onRowClick={(n) => onSelect({ type: "node", id: n.id })}
          selectedKey={selection?.type === "node" ? selection.id : null}
          caption={`Services impacted if ${targetName} changes`}
          virtualize={{ threshold: 60, maxHeight: 520 }}
        />
      )}
    </OverviewSection>
  );
}

// ---------------------------------------------------------------------------
// Breaking changes
// ---------------------------------------------------------------------------

function BreakingResults({ breaking, onSelect }: SystemMapLensResultsProps) {
  if (!breaking) return null;
  const sorted = sortChangesBySeverity(breaking.changes);
  return (
    <OverviewSection
      title="Breaking changes"
      description={`${breakingChangeSummary(breaking)} in the most recent update. A consumer is marked exposed when it links to the changed endpoint; that does not prove it uses the changed field.`}
    >
      <div className="border-t border-[var(--color-border-default)]">
        {sorted.map((change) => (
          <BreakingChangeRow
            key={breakingChangeKey(change)}
            change={change}
            onSelectNode={(id) => onSelect({ type: "node", id })}
          />
        ))}
      </div>
    </OverviewSection>
  );
}

// ---------------------------------------------------------------------------
// Conformance
// ---------------------------------------------------------------------------

function ConformanceResults({ conformance, graph, onSelect, onPrompt, conformanceHref, LinkComponent }: WithPrompt) {
  if (!conformance) return null;
  const name = (id: string) => graph.nodes.find((n) => n.id === id)?.name ?? id;
  const total = conformance.total_cycles ?? conformance.cycle_count;
  const A = LinkComponent ?? "a";
  const rules =
    conformance.rules_evaluated === 0
      ? "No dependency rules are declared, so only cycles are checked."
      : `${plural(conformance.rules_evaluated, "rule", "rules")} checked, ${plural(conformance.violation_count, "violation", "violations")}.`;
  const cycles =
    total > conformance.cycle_count
      ? `${conformance.cycle_count} of ${plural(total, "dependency cycle", "dependency cycles")} listed.`
      : `${plural(conformance.cycle_count, "dependency cycle", "dependency cycles")}.`;

  return (
    <OverviewSection
      title="Conformance"
      description={`${rules} ${cycles}`}
      action={
        conformanceHref ? (
          <A href={conformanceHref} className="whitespace-nowrap text-xs font-medium text-[var(--color-accent-primary)] hover:underline">
            Dependency matrix →
          </A>
        ) : undefined
      }
    >
      <ul className="m-0 flex list-none flex-col divide-y divide-[var(--color-border-default)] border-t border-[var(--color-border-default)] p-0">
        {conformance.violations.length > 0 && (
          <li className="flex flex-wrap items-center justify-between gap-3 py-3">
            <span className="text-[15px] text-[var(--color-text-primary)]">
              {plural(conformance.violations.length, "dependency breaks", "dependencies break")} a declared rule.
            </span>
            <AiPromptButton
              label="Fix violations"
              onClick={() =>
                onPrompt({
                  title: "AI conformance fix",
                  description: "A ready-to-paste prompt that has your agent remove the dependencies your rules forbid.",
                  build: (flavor) => buildConformanceAiPrompt({ violations: conformance.violations, flavor }),
                })
              }
            />
          </li>
        )}
        {conformance.violations.map((v) => (
          <li key={`${v.edge_id}:${v.rule_source}:${v.rule_target}`} className="flex flex-wrap items-baseline gap-x-2 gap-y-1 py-2.5 text-[15px]">
            <span className="inline-flex items-center gap-1.5">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-[var(--color-error)]" />
              <span className="text-xs text-[var(--color-text-secondary)]">Violation</span>
            </span>
            <NodeLink id={v.source} label={v.source_name || name(v.source)} onSelect={onSelect} />
            <span aria-hidden>→</span>
            <NodeLink id={v.target} label={v.target_name || name(v.target)} onSelect={onSelect} />
            <span className="font-mono text-xs text-[var(--color-text-tertiary)]">
              breaks {v.rule_source} !-&gt; {v.rule_target}
            </span>
          </li>
        ))}
        {conformance.cycles.map((c) => (
          <CycleRow key={c.edge_ids.join("|")} cycle={c} graph={graph} onSelect={onSelect} onPrompt={onPrompt} />
        ))}
      </ul>
    </OverviewSection>
  );
}

export function NodeLink({
  id,
  label,
  onSelect,
}: {
  id: string;
  label: string;
  onSelect: (s: SystemMapSelection) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect({ type: "node", id })}
      className="cursor-pointer font-medium text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
    >
      {label}
    </button>
  );
}

export function CycleRow({
  cycle,
  graph,
  onSelect,
  onPrompt,
}: {
  cycle: ConformanceReport["cycles"][number];
  graph: SystemGraph;
  onSelect: (s: SystemMapSelection) => void;
  onPrompt: (p: SystemMapPromptState) => void;
}) {
  const name = (id: string) => graph.nodes.find((n) => n.id === id)?.name ?? id;
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="flex min-w-0 flex-col gap-1">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[15px] text-[var(--color-text-primary)]">
          <span className="inline-flex items-center gap-1.5">
            <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-[var(--color-warning)]" />
            <span className="text-xs text-[var(--color-text-secondary)]">Cycle</span>
          </span>
          {cycle.nodes.map((id) => (
            <span key={id} className="inline-flex items-center gap-2">
              <NodeLink id={id} label={name(id)} onSelect={onSelect} />
              <span aria-hidden className="text-[var(--color-text-tertiary)]">→</span>
            </span>
          ))}
          <NodeLink id={cycle.nodes[0] ?? ""} label={name(cycle.nodes[0] ?? "")} onSelect={onSelect} />
        </span>
        <span className="flex flex-wrap gap-x-3 text-xs text-[var(--color-text-secondary)]">
          {`${plural(cycle.length, "service depends", "services depend")} on each other, so none can change a contract, build or deploy alone. Edges:`}
          {cycle.edge_ids.map((eid) => {
            const e = graph.edges.find((x) => x.id === eid);
            return (
              <button
                key={eid}
                type="button"
                onClick={() => onSelect({ type: "edge", id: eid })}
                className="cursor-pointer font-mono text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
              >
                {e ? `${name(e.source)} → ${name(e.target)} ${edgeKindStyle(e.kind).label}` : eid}
              </button>
            );
          })}
        </span>
      </div>
      <AiPromptButton label="Break the cycle" onClick={() => onPrompt(cyclePrompt(cycle, graph))} />
    </li>
  );
}

// ---------------------------------------------------------------------------
// Core and roles
// ---------------------------------------------------------------------------

function CoreResults({ architecture, graph, selection, onSelect }: SystemMapLensResultsProps) {
  if (!architecture) return null;
  const name = (id: string) => graph.nodes.find((n) => n.id === id)?.name ?? id;
  const roles = [...architecture.roles].sort(
    (a, b) =>
      ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role) ||
      b.visibility_fan_in - a.visibility_fan_in ||
      a.name.localeCompare(b.name),
  );
  const breakdown = ROLE_ORDER.filter((r) => (architecture.role_breakdown[r] ?? 0) > 0)
    .map((r) => `${architecture.role_breakdown[r]} ${roleStyle(r).label.toLowerCase()}`)
    .join(", ");
  const core = architecture.core_members.map(name);
  const description =
    architecture.core_size === 0
      ? `No services form a cycle, so there is no cyclic core. Roles: ${breakdown}.`
      : `${core.join(" and ")} form the cyclic core: ${architecture.core_size} of ${architecture.node_count} services that all reach each other through contracts or imports. Roles: ${breakdown}. Reach counts other services along structural dependencies.`;

  const columns: ResponsiveColumn<NodeArchitectureRole>[] = [
    {
      key: "name",
      header: "Service",
      priority: 1,
      render: (r) => (
        <span className="flex flex-col">
          <span className="text-[15px] font-medium text-[var(--color-text-primary)]">{r.name}</span>
          <span className="font-mono text-[10px] text-[var(--color-text-tertiary)]">{r.repo}</span>
        </span>
      ),
    },
    {
      key: "role",
      header: "Role",
      priority: 1,
      render: (r) => (
        <span title={roleStyle(r.role).description} className="text-[var(--color-text-primary)]">
          {roleStyle(r.role).label}
        </span>
      ),
    },
    {
      key: "reaches",
      header: "Reaches",
      align: "right",
      priority: 2,
      render: (r) => <span className="tabular-nums">{Math.max(r.visibility_fan_out - 1, 0)}</span>,
    },
    {
      key: "reached",
      header: "Reached by",
      align: "right",
      priority: 2,
      render: (r) => <span className="tabular-nums">{Math.max(r.visibility_fan_in - 1, 0)}</span>,
    },
  ];

  return (
    <OverviewSection title="Architectural core and roles" description={description}>
      <ResponsiveTable
        columns={columns}
        rows={roles}
        rowKey={(r) => r.id}
        onRowClick={(r) => onSelect({ type: "node", id: r.id })}
        selectedKey={selection?.type === "node" ? selection.id : null}
        caption="Architecture role of each service"
        virtualize={{ threshold: 60, maxHeight: 520 }}
      />
    </OverviewSection>
  );
}
