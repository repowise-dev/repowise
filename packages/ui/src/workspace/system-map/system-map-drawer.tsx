"use client";

/**
 * The System Map's detail drawer: one service or one relationship, with
 * everything needed to act on it. It is the shared `AdaptivePanel`, non-modal,
 * so the map stays interactive behind it and a click on another node swaps the
 * subject without closing and reopening.
 *
 * Resolves the selection against the *drawn* graph. In repo view the ids the
 * map hands back are collapsed ones that only exist there.
 *
 * Presentation only. The host fetches the focused repository's contracts and
 * owns every route; the drawer takes href builders, not a router.
 */

import { useMemo, useState, type ElementType, type ReactNode } from "react";
import type {
  DependencyCycle,
  NodeArchitectureRole,
  SystemEdge,
  SystemGraph,
  SystemNode,
  WorkspaceContractLinkEntry,
} from "@repowise-dev/types/workspace";
import { AdaptivePanel } from "../../shared/adaptive-panel";
import { AiPromptButton } from "../../health/ai-prompt-button";
import { AiPromptModal } from "../../health/ai-prompt-modal";
import type { AiPromptFlavor } from "../../health/ai-prompt-builder";
import { roleStyle } from "./architecture";
import { edgeKindStyle, matchTypeLabel } from "./edge-kinds";
import { nodeKindStyle } from "./node-kinds";
import { MICRO_LABEL } from "./system-map-filters";
import {
  BEHAVIORAL_MEANING,
  STRUCTURAL_MEANING,
  coChangePairs,
  cyclesThrough,
  cyclesWithEdge,
  edgeLinks,
  edgeSentence,
  healthMark,
  neighbourGroups,
  nodeLocation,
  plural,
  serviceDiagnostics,
  serviceResolver,
  summarizeServiceContracts,
  uniqueRefs,
  unmatchedReasonList,
  weightLabel,
  type ServiceResolver,
  type SystemMapRepoContracts,
} from "./system-map-model";
import {
  buildCycleAiPrompt,
  buildEdgeAiPrompt,
  buildServiceAiPrompt,
} from "./system-map-ai-prompt";
import type { RepoHealth, SystemMapSelection } from "./types";

/** Contracts listed in the drawer before it hands off to the Contracts page. */
export const DRAWER_CONTRACT_ROWS = 8;
/** Evidence rows listed on an edge before it hands off. */
export const DRAWER_EVIDENCE_ROWS = 20;

export interface ContractRef {
  contract_id: string;
  repo: string;
  file_path?: string | undefined;
}

/** What the host supplies beyond the graph: data it fetched and its routes. */
export interface SystemMapDrawerData {
  /** Contracts of the repository the selection lives in (see `selectionRepo`). */
  repoContracts?: SystemMapRepoContracts | null | undefined;
  repoContractsLoading?: boolean | undefined;
  cycles?: readonly DependencyCycle[] | undefined;
  contractHref?: ((ref: ContractRef) => string) | undefined;
  /** The Contracts page filtered to one repository. */
  contractsHref?: ((repo: string) => string) | undefined;
  repoHref?: ((repo: string) => string | null) | undefined;
  coChangesHref?: string | undefined;
  LinkComponent?: ElementType | undefined;
  onShowBlastRadius?: ((nodeId: string) => void) | undefined;
}

export interface SystemMapDrawerProps extends SystemMapDrawerData {
  selection: SystemMapSelection;
  /** The drawn graph (collapse + filters applied). */
  graph: SystemGraph;
  /** The raw graph, for diagnostics and service boundaries. */
  rawGraph: SystemGraph;
  collapsed: boolean;
  healthByRepo?: ReadonlyMap<string, RepoHealth> | undefined;
  roleByNodeId?: ReadonlyMap<string, NodeArchitectureRole> | undefined;
  onClose: () => void;
  onSelect: (selection: SystemMapSelection) => void;
}

/**
 * The repository whose contracts the drawer needs for a selection, so the host
 * can fetch before the drawer asks. Accepts raw and collapsed ids.
 */
export function selectionRepo(graph: SystemGraph | null, selection: SystemMapSelection): string | null {
  if (!graph || !selection) return null;
  const repoOfNode = (id: string) =>
    graph.nodes.find((n) => n.id === id)?.repo ?? (graph.nodes.some((n) => n.repo === id) ? id : null);
  if (selection.type === "node") return repoOfNode(selection.id);
  const edge = graph.edges.find((e) => e.id === selection.id);
  if (edge) return repoOfNode(edge.source);
  const source = selection.id.split("->")[0];
  return source ? repoOfNode(source) : null;
}

interface PromptState {
  title: string;
  description: string;
  build: (flavor: AiPromptFlavor) => string;
}

export function SystemMapDrawer(props: SystemMapDrawerProps) {
  const { selection, graph, rawGraph, collapsed, onClose } = props;
  const [prompt, setPrompt] = useState<PromptState | null>(null);
  const resolve = useMemo(() => serviceResolver(rawGraph, collapsed), [rawGraph, collapsed]);

  const node = selection?.type === "node" ? graph.nodes.find((n) => n.id === selection.id) ?? null : null;
  const edge = selection?.type === "edge" ? graph.edges.find((e) => e.id === selection.id) ?? null : null;
  const open = Boolean(node || edge);
  const name = (id: string) => graph.nodes.find((n) => n.id === id)?.name ?? id;

  return (
    <>
      <AdaptivePanel
        open={open}
        onOpenChange={(next) => {
          if (!next) onClose();
        }}
        modal={false}
        // The map, the rows below it and the drawer are one workspace: a click
        // elsewhere selects something else or does nothing, never closes this.
        onInteractOutside={(e) => e.preventDefault()}
        eyebrow={node ? nodeKindStyle(node.kind).label : "Relationship"}
        title={node ? node.name : edge ? `${name(edge.source)} → ${name(edge.target)}` : ""}
        widthClassName="md:max-w-[480px]"
      >
        {node && <NodeBody {...props} node={node} resolve={resolve} onPrompt={setPrompt} />}
        {edge && <EdgeBody {...props} edge={edge} resolve={resolve} onPrompt={setPrompt} />}
      </AdaptivePanel>
      <AiPromptModal
        open={prompt !== null}
        onOpenChange={(o) => !o && setPrompt(null)}
        getPrompt={prompt?.build ?? null}
        {...(prompt ? { title: prompt.title, description: prompt.description } : {})}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className={MICRO_LABEL}>{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Facts({ items }: { items: { label: string; value: ReactNode }[] }) {
  return (
    <dl className="grid grid-cols-2 border-y border-[var(--color-border-default)]">
      {items.map((f, i) => (
        <div
          key={f.label}
          className={[
            "min-w-0 px-3 py-2.5 border-[var(--color-border-default)]",
            i % 2 === 1 ? "border-l" : "",
            i >= 2 ? "border-t" : "",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          <dt className={MICRO_LABEL}>{f.label}</dt>
          <dd className="mt-1 text-[15px] text-[var(--color-text-primary)]">{f.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Anchor({
  href,
  LinkComponent,
  children,
  className,
}: {
  href: string;
  LinkComponent?: ElementType | undefined;
  children: ReactNode;
  className?: string;
}) {
  const A = LinkComponent ?? "a";
  return (
    <A
      href={href}
      className={`text-[var(--color-accent-primary)] hover:underline ${className ?? ""}`}
    >
      {children}
    </A>
  );
}

function SelectButton({ onClick, children, title }: { onClick: () => void; children: ReactNode; title?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="cursor-pointer text-left font-medium text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
    >
      {children}
    </button>
  );
}

function distinct(groups: { edges: SystemEdge[] }[], pick: (e: SystemEdge) => string): number {
  return new Set(groups.flatMap((g) => g.edges.map(pick))).size;
}

type BodyProps<T> = SystemMapDrawerProps & {
  resolve: ServiceResolver;
  onPrompt: (p: PromptState) => void;
} & T;

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

function NodeBody({
  node,
  graph,
  rawGraph,
  collapsed,
  healthByRepo,
  roleByNodeId,
  repoContracts,
  repoContractsLoading,
  cycles = [],
  contractHref,
  contractsHref,
  repoHref,
  LinkComponent,
  onShowBlastRadius,
  onSelect,
  resolve,
  onPrompt,
}: BodyProps<{ node: SystemNode }>) {
  const health = healthByRepo?.get(node.repo);
  const mark = health ? healthMark(health.score) : null;
  // Roles are computed per service, so a merged repo node has none.
  const role = collapsed ? undefined : roleByNodeId?.get(node.id);
  const style = role ? roleStyle(role.role) : null;
  const reaches = role ? Math.max(role.visibility_fan_out - 1, 0) : 0;
  const reachedBy = role ? Math.max(role.visibility_fan_in - 1, 0) : 0;

  const out = neighbourGroups(graph, node.id, "out");
  const incoming = neighbourGroups(graph, node.id, "in");
  const myCycles = collapsed ? [] : cyclesThrough(cycles, node.id);
  const diag = serviceDiagnostics(rawGraph.diagnostics, node.id, resolve);
  const summary = useMemo(
    () => (repoContracts ? summarizeServiceContracts(repoContracts, node.id, resolve) : null),
    [repoContracts, node.id, resolve],
  );
  const capped = repoContracts ? repoContracts.total > repoContracts.contracts.length : false;
  const name = (id: string) => graph.nodes.find((n) => n.id === id)?.name ?? id;
  const overview = repoHref?.(node.repo) ?? null;

  const neighbours = (groups: typeof out, pick: (e: SystemEdge) => string) =>
    groups.flatMap((g) =>
      g.edges.map((e) => ({
        id: pick(e),
        name: name(pick(e)),
        kind: edgeKindStyle(e.kind).label,
        weight: e.weight,
        weight_label: weightLabel(e),
      })),
    );

  const openServicePrompt = () =>
    onPrompt({
      title: "Map this service before a change",
      description: `A ready-to-paste prompt that has your agent map what ${node.name} exposes and who would feel a change, before anyone edits it.`,
      build: (flavor) =>
        buildServiceAiPrompt({
          service: { id: node.id, name: node.name, repo: node.repo, service_path: node.service_path },
          health: mark ? { value: mark.value, label: mark.label } : null,
          role: style ? { label: style.label, reaches, reachedBy } : null,
          provides: summary?.provides ?? {},
          consumes: summary?.consumes ?? {},
          contracts: summary?.rows ?? [],
          contractTotal: summary ? summary.providedTotal + summary.consumedTotal : node.provider_count + node.consumer_count,
          dependsOn: neighbours(out, (e) => e.target),
          dependedOnBy: neighbours(incoming, (e) => e.source),
          unmatched: diag.unmatched,
          unmatchedByReason: diag.unmatchedByReason,
          unusedProviders: diag.unusedProviders,
          cycles: myCycles.map((c) => c.nodes.map(name)),
          flavor,
        }),
    });

  return (
    <div className="flex flex-col gap-6 px-4 py-4">
      <div className="flex flex-col gap-2">
        <p className="font-mono text-xs text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
          {nodeLocation(node)}
        </p>
        <p className="text-[15px] leading-relaxed text-[var(--color-text-primary)] [text-wrap:pretty]">
          {node.name} provides {plural(node.provider_count, "contract", "contracts")} and consumes{" "}
          {node.consumer_count.toLocaleString()}
          {node.contract_types.length > 0 ? ` (${node.contract_types.join(", ")})` : ""}.
          {out.length + incoming.length === 0 && " It has no relationships with other services."}
        </p>
      </div>

      <Facts
        items={[
          {
            label: "Repository health",
            value: mark ? (
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: mark.color }} />
                <span className="font-semibold tabular-nums">{mark.value}</span>
                <span className="text-xs text-[var(--color-text-secondary)]">{mark.label}</span>
              </span>
            ) : (
              <span className="text-xs text-[var(--color-text-tertiary)]">Not scored yet</span>
            ),
          },
          {
            label: "Architecture role",
            value: style ? style.label : <span className="text-xs text-[var(--color-text-tertiary)]">{collapsed ? "Per service only" : "Not computed"}</span>,
          },
          { label: "Outgoing to", value: <span className="tabular-nums">{plural(distinct(out, (e) => e.target), "service", "services")}</span> },
          { label: "Incoming from", value: <span className="tabular-nums">{plural(distinct(incoming, (e) => e.source), "service", "services")}</span> },
        ]}
      />

      {(style || mark) && (
        <p className="-mt-3 text-xs leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
          {style &&
            `${style.label}: ${style.description} Through structural dependencies it reaches ${plural(reaches, "other service", "other services")}, and ${plural(reachedBy, "service reaches", "services reach")} it. `}
          {mark && `Health is the ${node.repo} repository's score, shared by every service in it.`}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {onShowBlastRadius && (
          <button
            type="button"
            onClick={() => onShowBlastRadius(node.id)}
            className="rounded-md border border-[var(--color-border-hover)] px-2.5 py-1 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          >
            Show blast radius
          </button>
        )}
        <AiPromptButton label="Map before a change" onClick={openServicePrompt} />
        {overview && (
          <Anchor href={overview} LinkComponent={LinkComponent} className="text-xs font-medium">
            {node.repo} overview →
          </Anchor>
        )}
      </div>

      {(myCycles.length > 0 || diag.unmatched > 0 || diag.unusedProviders > 0) && (
        <Section title="Findings">
          <ul className="m-0 flex list-none flex-col gap-2 p-0 text-xs leading-relaxed text-[var(--color-text-secondary)]">
            {myCycles.map((c) => (
              <li key={c.edge_ids.join("|")} className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="inline-flex items-center gap-1.5">
                  <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-[var(--color-warning)]" />
                  In a dependency cycle:
                </span>
                {c.nodes.map((id, i) => (
                  <span key={id} className="inline-flex items-center gap-1">
                    {id === node.id ? (
                      <span className="font-medium text-[var(--color-text-primary)]">{name(id)}</span>
                    ) : (
                      <SelectButton onClick={() => onSelect({ type: "node", id })}>{name(id)}</SelectButton>
                    )}
                    <span aria-hidden>{i < c.nodes.length - 1 ? "→" : "↩"}</span>
                  </span>
                ))}
                <AiPromptButton
                  variant="icon"
                  label="Break this cycle with AI"
                  onClick={() => onPrompt(cyclePrompt(c, rawGraph))}
                />
              </li>
            ))}
            {diag.unmatched > 0 && (
              <li>
                {plural(diag.unmatched, "call", "calls")} from here match no provider in the workspace:{" "}
                {unmatchedReasonList(diag.unmatchedByReason)}.
              </li>
            )}
            {diag.unusedProviders > 0 && (
              <li className="text-[var(--color-text-tertiary)]">
                {plural(diag.unusedProviders, "provided contract has", "provided contracts have")} no consumer in
                the workspace. Callers outside it are not counted.
              </li>
            )}
          </ul>
        </Section>
      )}

      <Section title="Outgoing: depends on or changes with">
        <NeighbourList groups={out} pick={(e) => e.target} name={name} onSelect={onSelect} empty="Nothing in the workspace." />
      </Section>
      <Section title="Incoming: depended on or changes with">
        <NeighbourList groups={incoming} pick={(e) => e.source} name={name} onSelect={onSelect} empty="Nothing in the workspace." />
      </Section>

      <Section
        title="Contracts"
        aside={
          contractsHref && (
            <Anchor href={contractsHref(node.repo)} LinkComponent={LinkComponent} className="text-xs font-medium">
              All in {node.repo} →
            </Anchor>
          )
        }
      >
        {repoContractsLoading && !summary ? (
          <p className="text-xs text-[var(--color-text-tertiary)]">Loading contracts…</p>
        ) : !summary ? (
          <p className="text-xs text-[var(--color-text-tertiary)]">Contract detail is not available here.</p>
        ) : summary.rows.length === 0 ? (
          <p className="text-xs text-[var(--color-text-tertiary)]">No contracts were extracted for this service.</p>
        ) : (
          <>
            <TypeTable provides={summary.provides} consumes={summary.consumes} />
            <ul className="m-0 mt-1 flex list-none flex-col divide-y divide-[var(--color-border-default)] p-0">
              {summary.rows.slice(0, DRAWER_CONTRACT_ROWS).map((c) => (
                <li key={`${c.file_path}:${c.contract_id}`} className="flex flex-col gap-0.5 py-2">
                  <div className="flex items-baseline justify-between gap-3">
                    {contractHref ? (
                      <Anchor
                        href={contractHref({ contract_id: c.contract_id, repo: c.repo, file_path: c.file_path })}
                        LinkComponent={LinkComponent}
                        className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]"
                      >
                        {c.contract_id}
                      </Anchor>
                    ) : (
                      <span className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]">{c.contract_id}</span>
                    )}
                    <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
                      {c.role} · {plural(c.link_count, "link", "links")}
                    </span>
                  </div>
                  <span className="font-mono text-[10px] text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
                    {c.file_path}
                    {c.line ? `:${c.line}` : ""}
                  </span>
                </li>
              ))}
            </ul>
            <p className="text-xs text-[var(--color-text-tertiary)]">
              {`Showing ${Math.min(summary.rows.length, DRAWER_CONTRACT_ROWS)} of ${summary.rows.length.toLocaleString()}, most linked first.`}
              {capped &&
                ` Counted from the first ${repoContracts!.contracts.length.toLocaleString()} of ${repoContracts!.total.toLocaleString()} contracts in ${node.repo}.`}
            </p>
          </>
        )}
      </Section>
    </div>
  );
}

function TypeTable({ provides, consumes }: { provides: Record<string, number>; consumes: Record<string, number> }) {
  const types = [...new Set([...Object.keys(provides), ...Object.keys(consumes)])].sort();
  return (
    <table className="w-full text-xs tabular-nums">
      <thead>
        <tr className={MICRO_LABEL}>
          <th className="py-1 text-left font-normal">Type</th>
          <th className="py-1 text-right font-normal">Provides</th>
          <th className="py-1 text-right font-normal">Consumes</th>
        </tr>
      </thead>
      <tbody>
        {types.map((t) => (
          <tr key={t} className="border-t border-[var(--color-border-default)]">
            <td className="py-1 font-mono text-[var(--color-text-secondary)]">{t}</td>
            <td className="py-1 text-right text-[var(--color-text-primary)]">{(provides[t] ?? 0).toLocaleString()}</td>
            <td className="py-1 text-right text-[var(--color-text-primary)]">{(consumes[t] ?? 0).toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function NeighbourList({
  groups,
  pick,
  name,
  onSelect,
  empty,
}: {
  groups: ReturnType<typeof neighbourGroups>;
  pick: (e: SystemEdge) => string;
  name: (id: string) => string;
  onSelect: (s: SystemMapSelection) => void;
  empty: string;
}) {
  if (groups.length === 0) return <p className="text-xs text-[var(--color-text-tertiary)]">{empty}</p>;
  return (
    <div className="flex flex-col gap-2">
      {groups.map((g) => {
        const s = edgeKindStyle(g.kind);
        const Icon = s.icon;
        return (
          <div key={g.kind} className="flex flex-col gap-1">
            <span className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)]">
              <Icon size={12} aria-hidden />
              {s.label} · {g.kind === "co_change" ? "behavioral" : "structural"}
            </span>
            <ul className="m-0 flex list-none flex-col p-0">
              {g.edges.map((e) => (
                <li key={e.id}>
                  <button
                    type="button"
                    onClick={() => onSelect({ type: "node", id: pick(e) })}
                    className="flex w-full cursor-pointer items-baseline justify-between gap-3 rounded px-1.5 py-1 text-left text-[15px] text-[var(--color-text-primary)] hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
                  >
                    <span className="min-w-0 [overflow-wrap:anywhere]">{name(pick(e))}</span>
                    <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
                      {weightLabel(e)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Relationship
// ---------------------------------------------------------------------------

const MATCH_MEANING: Record<string, string> = {
  exact: "the call resolves to exactly this provider",
  manual: "declared by hand in the workspace config",
  candidate: "a heuristic match; verify before relying on it",
  inferred: "inferred from commits, not from code",
};

function EdgeBody({
  edge,
  graph,
  rawGraph,
  repoContracts,
  repoContractsLoading,
  cycles = [],
  collapsed,
  contractHref,
  contractsHref,
  coChangesHref,
  LinkComponent,
  onSelect,
  resolve,
  onPrompt,
}: BodyProps<{ edge: SystemEdge }>) {
  const style = edgeKindStyle(edge.kind);
  const Icon = style.icon;
  const nodeOf = (id: string) => graph.nodes.find((n) => n.id === id);
  const source = nodeOf(edge.source);
  const target = nodeOf(edge.target);
  const sourceName = source?.name ?? edge.source;
  const targetName = target?.name ?? edge.target;
  const sentence = edgeSentence(edge, sourceName, targetName);

  const links = useMemo<WorkspaceContractLinkEntry[] | null>(
    () => (repoContracts && edge.structural ? edgeLinks(edge, repoContracts.links, resolve) : null),
    [repoContracts, edge, resolve],
  );
  const refs = uniqueRefs(edge);
  const pairs = edge.structural ? [] : coChangePairs(edge);
  const myCycles = collapsed ? [] : cyclesWithEdge(cycles, edge.id);
  const name = (id: string) => nodeOf(id)?.name ?? id;

  const openEdgePrompt = () =>
    onPrompt({
      title: edge.structural ? "Verify this dependency" : "Check this co-change",
      description: edge.structural
        ? `A ready-to-paste prompt that has your agent confirm ${sourceName} → ${targetName} in code and document it.`
        : `A ready-to-paste prompt that has your agent decide whether ${sourceName} and ${targetName} share a hidden dependency.`,
      build: (flavor) =>
        buildEdgeAiPrompt({
          edge,
          source: { id: edge.source, name: sourceName },
          target: { id: edge.target, name: targetName },
          sentence,
          weightLabel: weightLabel(edge),
          evidence: links ?? [],
          refs,
          pairs,
          cycles: myCycles.map((c) => c.nodes.map(name)),
          flavor,
        }),
    });

  return (
    <div className="flex flex-col gap-6 px-4 py-4">
      <div className="flex flex-col gap-2">
        <p className="inline-flex items-center gap-1.5 text-xs text-[var(--color-text-tertiary)]">
          <Icon size={12} aria-hidden />
          {style.label} · {edge.structural ? "structural" : "behavioral"}
        </p>
        <p className="text-[15px] leading-relaxed text-[var(--color-text-primary)] [text-wrap:pretty]">{sentence}</p>
        <p className="text-xs leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
          {edge.structural ? STRUCTURAL_MEANING : BEHAVIORAL_MEANING}
        </p>
      </div>

      <Facts
        items={[
          {
            label: "From",
            value: <SelectButton onClick={() => onSelect({ type: "node", id: edge.source })}>{sourceName}</SelectButton>,
          },
          {
            label: "To",
            value: <SelectButton onClick={() => onSelect({ type: "node", id: edge.target })}>{targetName}</SelectButton>,
          },
          {
            label: "Match",
            value: (
              <span title={MATCH_MEANING[edge.match_type]}>
                {matchTypeLabel(edge.match_type)}
                <span className="block text-xs text-[var(--color-text-tertiary)]">{MATCH_MEANING[edge.match_type]}</span>
              </span>
            ),
          },
          {
            label: "Confidence",
            value: <span className="tabular-nums">{Math.round(edge.confidence * 100)}%</span>,
          },
          { label: "Weight", value: <span className="tabular-nums">{weightLabel(edge)}</span> },
          {
            label: "Evidence kept",
            value: (
              <span className="tabular-nums">
                {plural(edge.structural ? refs.length : pairs.length, "reference", "references")}
              </span>
            ),
          },
        ]}
      />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <AiPromptButton
          label={edge.structural ? "Verify this dependency" : "Check this co-change"}
          onClick={openEdgePrompt}
        />
      </div>

      {myCycles.length > 0 && (
        <Section title="Dependency cycle">
          {myCycles.map((c) => (
            <div key={c.edge_ids.join("|")} className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-secondary)]">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-[var(--color-warning)]" />
              This edge closes a cycle: {c.nodes.map(name).join(" → ")} → {name(c.nodes[0] ?? "")}.
              <AiPromptButton label="Break the cycle" onClick={() => onPrompt(cyclePrompt(c, rawGraph))} />
            </div>
          ))}
        </Section>
      )}

      {edge.structural ? (
        <Section
          title="Contract evidence"
          aside={
            contractsHref &&
            target && (
              <Anchor href={contractsHref(target.repo)} LinkComponent={LinkComponent} className="text-xs font-medium">
                All in {target.repo} →
              </Anchor>
            )
          }
        >
          {repoContractsLoading && !links ? (
            <p className="text-xs text-[var(--color-text-tertiary)]">Loading the links behind this edge…</p>
          ) : links && links.length > 0 ? (
            <>
              <ul className="m-0 flex list-none flex-col divide-y divide-[var(--color-border-default)] p-0">
                {links.slice(0, DRAWER_EVIDENCE_ROWS).map((l) => (
                  <li key={`${l.contract_id}|${l.provider_file}|${l.consumer_file}`} className="flex flex-col gap-0.5 py-2">
                    {contractHref ? (
                      <Anchor
                        href={contractHref({ contract_id: l.contract_id, repo: l.provider_repo, file_path: l.provider_file })}
                        LinkComponent={LinkComponent}
                        className="font-mono text-xs [overflow-wrap:anywhere]"
                      >
                        {l.contract_id}
                      </Anchor>
                    ) : (
                      <span className="font-mono text-xs [overflow-wrap:anywhere]">{l.contract_id}</span>
                    )}
                    <span className="font-mono text-[10px] text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
                      provides {l.provider_repo}/{l.provider_file}
                    </span>
                    <span className="font-mono text-[10px] text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
                      calls from {l.consumer_repo}/{l.consumer_file}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="text-xs text-[var(--color-text-tertiary)]">
                {`Showing ${Math.min(links.length, DRAWER_EVIDENCE_ROWS)} of ${plural(links.length, "matched link", "matched links")}.`}
              </p>
            </>
          ) : refs.length > 0 ? (
            <>
              <ul className="m-0 flex list-none flex-col gap-1 p-0">
                {refs.slice(0, DRAWER_EVIDENCE_ROWS).map((ref) => (
                  <li key={ref}>
                    {contractHref && target ? (
                      <Anchor
                        href={contractHref({ contract_id: ref, repo: target.repo })}
                        LinkComponent={LinkComponent}
                        className="font-mono text-xs [overflow-wrap:anywhere]"
                      >
                        {ref}
                      </Anchor>
                    ) : (
                      <span className="font-mono text-xs [overflow-wrap:anywhere]">{ref}</span>
                    )}
                  </li>
                ))}
              </ul>
              <p className="text-xs text-[var(--color-text-tertiary)]">
                {`Showing ${Math.min(refs.length, DRAWER_EVIDENCE_ROWS)} of ${refs.length} contract ids the graph kept for this edge; it aggregates ${weightLabel(edge)}.`}
              </p>
            </>
          ) : (
            <p className="text-xs text-[var(--color-text-tertiary)]">The graph kept no evidence for this edge.</p>
          )}
        </Section>
      ) : (
        <Section
          title="Files that changed together"
          aside={
            coChangesHref && (
              <Anchor href={coChangesHref} LinkComponent={LinkComponent} className="text-xs font-medium">
                All co-changes →
              </Anchor>
            )
          }
        >
          {pairs.length === 0 ? (
            <p className="text-xs text-[var(--color-text-tertiary)]">The graph kept no file pairs for this edge.</p>
          ) : (
            <>
              <ul className="m-0 flex list-none flex-col divide-y divide-[var(--color-border-default)] p-0">
                {pairs.slice(0, DRAWER_EVIDENCE_ROWS).map(([a, b]) => (
                  <li key={`${a}~${b}`} className="flex flex-col gap-0.5 py-1.5 font-mono text-[10px] leading-snug">
                    <span className="text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
                      {sourceName}/{a}
                    </span>
                    {b && (
                      <span className="text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
                        with {targetName}/{b}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
              <p className="text-xs text-[var(--color-text-tertiary)]">
                {`Showing ${Math.min(pairs.length, DRAWER_EVIDENCE_ROWS)} of ${plural(edge.weight, "file pair", "file pairs")}.`}
              </p>
            </>
          )}
        </Section>
      )}
    </div>
  );
}

/** The cycle prompt, shared by the drawer and the findings below the map. */
export function cyclePrompt(cycle: DependencyCycle, graph: SystemGraph): PromptState {
  const name = (id: string) => graph.nodes.find((n) => n.id === id)?.name ?? id;
  const names = cycle.nodes.map(name);
  return {
    title: "Break this dependency cycle",
    description: `A ready-to-paste prompt that has your agent find the edge that closes ${names.join(" → ")} and propose how to cut it.`,
    build: (flavor) =>
      buildCycleAiPrompt({
        names,
        ids: cycle.nodes,
        edges: cycle.edge_ids.map((id) => {
          const e = graph.edges.find((x) => x.id === id);
          return {
            id,
            source: e ? name(e.source) : id,
            target: e ? name(e.target) : "",
            kind: e?.kind ?? "unknown",
            weight_label: e ? weightLabel(e) : "unknown weight",
            refs: e ? uniqueRefs(e) : [],
          };
        }),
        flavor,
      }),
  };
}

export type { PromptState as SystemMapPromptState };
