"use client";

/**
 * Selection detail for the Live System Map: a service node (health, role,
 * fan-in/out, neighbours) or an edge (kind, match, confidence, and the evidence
 * behind it). Rendered as a card in the map's rail — it used to float over the
 * canvas at the same corner two other panels also claimed.
 *
 * Resolves the selection against the *view* graph. In repo view the ids the map
 * hands back are collapsed ones, which either miss the raw graph entirely (edge
 * ids differ) or hit a same-named service node holding un-merged numbers.
 */

import type {
  NodeArchitectureRole,
  SystemEdge,
  SystemGraph,
  SystemNode,
} from "@repowise-dev/types";
import { HealthRing } from "../workspace-graph-node";
import { roleStyle } from "./architecture";
import { edgeKindStyle, matchTypeLabel } from "./edge-kinds";
import { nodeKindStyle } from "./node-kinds";
import { RailEyebrow, RailField, SystemMapRailPanel } from "./system-map-rail";
import type { RepoHealth, SystemMapSelection } from "./types";

export interface SystemMapInspectorProps {
  selection: SystemMapSelection;
  /** The graph on screen (collapse + filters applied), not the raw graph. */
  graph: SystemGraph;
  healthByRepo?: ReadonlyMap<string, RepoHealth>;
  /** Per-service architecture role + visibility profile (Phase 6, optional). */
  roleByNodeId?: ReadonlyMap<string, NodeArchitectureRole>;
  onClose: () => void;
  /** Select another node (e.g. clicking a connected service). */
  onSelectNode: (nodeId: string) => void;
  /** Open a contract on the Contracts surface (drill to both code sides). */
  onOpenContract?: (contractId: string) => void;
}

function NodeBody({
  node,
  graph,
  health,
  role,
  onSelectNode,
}: {
  node: SystemNode;
  graph: SystemGraph;
  health: RepoHealth | null;
  role: NodeArchitectureRole | null;
  onSelectNode: (id: string) => void;
}) {
  const kind = nodeKindStyle(node.kind);
  const outgoing = graph.edges.filter((e) => e.source === node.id);
  const incoming = graph.edges.filter((e) => e.target === node.id);
  const neighbour = (id: string) => graph.nodes.find((n) => n.id === id)?.name ?? id;

  return (
    <div className="flex flex-col gap-2 px-3 py-2.5">
      <div className="flex items-center gap-2.5">
        {health && <HealthRing score={health.score} source={health.source} size={36} />}
        <div className="min-w-0">
          <div className="text-sm font-semibold text-[var(--color-text-primary)]">{node.name}</div>
          <div className="text-[11px] text-[var(--color-text-tertiary)]">
            {kind.label} · {node.repo}
          </div>
        </div>
      </div>
      <div>
        {role && (
          <RailField
            label="Role"
            value={
              <span
                title={roleStyle(role.role).description}
                className="inline-flex items-center gap-1.5"
              >
                <span
                  className="h-[7px] w-[7px] rounded-full"
                  style={{ background: roleStyle(role.role).color }}
                />
                {roleStyle(role.role).label}
              </span>
            }
          />
        )}
        {role && (
          <RailField
            label="Visibility (in / out)"
            value={`${role.visibility_fan_in} / ${role.visibility_fan_out}`}
          />
        )}
        {node.service_path && (
          <RailField
            label="Path"
            value={<span className="font-mono text-[11px]">{node.service_path}</span>}
          />
        )}
        <RailField label="Provides" value={`${node.provider_count} contracts`} />
        <RailField label="Consumes" value={`${node.consumer_count} contracts`} />
        <RailField
          label="Types"
          value={node.contract_types.length ? node.contract_types.join(", ") : "none"}
        />
        {node.is_orphan_provider && (
          <RailField
            label="Status"
            value={<span className="text-[var(--color-warning)]">Orphan provider</span>}
          />
        )}
        {node.is_orphan_consumer && (
          <RailField
            label="Status"
            value={<span className="text-[var(--color-warning)]">Orphan consumer</span>}
          />
        )}
        {node.is_isolated && (
          <RailField
            label="Status"
            value={<span className="text-[var(--color-text-tertiary)]">Isolated</span>}
          />
        )}
      </div>
      <NeighbourList
        title={`Depends on (${outgoing.length})`}
        edges={outgoing}
        resolve={(e) => e.target}
        neighbour={neighbour}
        onSelectNode={onSelectNode}
      />
      <NeighbourList
        title={`Depended on by (${incoming.length})`}
        edges={incoming}
        resolve={(e) => e.source}
        neighbour={neighbour}
        onSelectNode={onSelectNode}
      />
    </div>
  );
}

function NeighbourList({
  title,
  edges,
  resolve,
  neighbour,
  onSelectNode,
}: {
  title: string;
  edges: SystemEdge[];
  resolve: (e: SystemEdge) => string;
  neighbour: (id: string) => string;
  onSelectNode: (id: string) => void;
}) {
  if (edges.length === 0) return null;
  return (
    <div>
      <div className="mb-[3px]">
        <RailEyebrow>{title}</RailEyebrow>
      </div>
      <div className="flex flex-col gap-0.5">
        {edges.map((e) => {
          const id = resolve(e);
          const s = edgeKindStyle(e.kind);
          return (
            <button
              key={e.id}
              type="button"
              onClick={() => onSelectNode(id)}
              className="flex cursor-pointer items-center gap-1.5 rounded-[var(--radius-sm)] px-1 py-0.5 text-left text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-overlay)]"
            >
              <span
                className="h-[7px] w-[7px] shrink-0 rounded-full"
                style={{ background: s.color }}
              />
              <span className="flex-1 truncate">{neighbour(id)}</span>
              <span className="text-[10px] text-[var(--color-text-tertiary)]">{s.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * Evidence behind an edge.
 *
 * Structural edges cite contract ids (`http::POST::/feedback`), which the
 * Contracts surface can resolve. Behavioral (co-change) edges cite
 * `source_file~target_file` pairs, which it cannot — every ref used to be
 * rendered as a button captioned "Open on the Contracts page", promising a
 * destination that could never answer. Those are shown as the file pair they
 * are.
 */
function EdgeEvidence({
  edge,
  onOpenContract,
}: {
  edge: SystemEdge;
  onOpenContract?: (contractId: string) => void;
}) {
  if (edge.contract_refs.length === 0) return null;

  // Back-pointers are capped per edge while `weight` counts every contributor,
  // so the two diverge once an edge is busy enough. Name the cap instead of
  // showing two figures the reader has to reconcile.
  const count =
    edge.weight > edge.contract_refs.length
      ? `${edge.contract_refs.length} of ${edge.weight}`
      : `${edge.contract_refs.length}`;

  if (!edge.structural) {
    return (
      <div>
        <div className="mb-[3px]">
          <RailEyebrow>{`Co-changed files (${count})`}</RailEyebrow>
        </div>
        <div className="flex flex-col gap-1">
          {edge.contract_refs.map((ref) => {
            const split = ref.indexOf("~");
            const source = split === -1 ? ref : ref.slice(0, split);
            const target = split === -1 ? null : ref.slice(split + 1);
            return (
              <div key={ref} className="font-mono text-[10.5px] leading-tight">
                <div className="truncate text-[var(--color-text-secondary)]" title={source}>
                  {source}
                </div>
                {target && (
                  <div className="truncate text-[var(--color-text-tertiary)]" title={target}>
                    ↔ {target}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-[3px]">
        <RailEyebrow>{`Evidence (${count})`}</RailEyebrow>
      </div>
      <div className="flex flex-col gap-0.5">
        {edge.contract_refs.map((ref) => (
          <button
            key={ref}
            type="button"
            onClick={onOpenContract ? () => onOpenContract(ref) : undefined}
            disabled={!onOpenContract}
            title={onOpenContract ? `Find ${ref} on the Contracts page` : ref}
            className={`truncate text-left font-mono text-[10.5px] ${
              onOpenContract
                ? "cursor-pointer text-[var(--color-accent-primary)] hover:underline"
                : "cursor-default text-[var(--color-text-tertiary)]"
            }`}
          >
            {ref}
          </button>
        ))}
      </div>
    </div>
  );
}

function EdgeBody({
  edge,
  graph,
  onSelectNode,
  onOpenContract,
}: {
  edge: SystemEdge;
  graph: SystemGraph;
  onSelectNode: (id: string) => void;
  onOpenContract?: (contractId: string) => void;
}) {
  const s = edgeKindStyle(edge.kind);
  const name = (id: string) => graph.nodes.find((n) => n.id === id)?.name ?? id;
  const Icon = s.icon;
  return (
    <div className="flex flex-col gap-2 px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-[13px] text-[var(--color-text-primary)]">
        <Icon size={13} style={{ color: s.color }} aria-hidden />
        {s.label} relationship
      </div>
      <div>
        <RailField
          label="From"
          value={<LinkText label={name(edge.source)} onClick={() => onSelectNode(edge.source)} />}
        />
        <RailField
          label="To"
          value={<LinkText label={name(edge.target)} onClick={() => onSelectNode(edge.target)} />}
        />
        <RailField label="Match" value={matchTypeLabel(edge.match_type)} />
        <RailField label="Confidence" value={`${Math.round(edge.confidence * 100)}%`} />
        <RailField label="Weight" value={String(edge.weight)} />
        <RailField label="Nature" value={edge.structural ? "Structural" : "Behavioral"} />
      </div>
      <EdgeEvidence edge={edge} {...(onOpenContract ? { onOpenContract } : {})} />
    </div>
  );
}

function LinkText({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="cursor-pointer text-[var(--color-accent-primary)] hover:underline"
    >
      {label}
    </button>
  );
}

export function SystemMapInspector({
  selection,
  graph,
  healthByRepo,
  roleByNodeId,
  onClose,
  onSelectNode,
  onOpenContract,
}: SystemMapInspectorProps) {
  if (!selection) return null;

  if (selection.type === "node") {
    const node = graph.nodes.find((n) => n.id === selection.id);
    if (!node) return null;
    return (
      <SystemMapRailPanel eyebrow="Service" onClear={onClose} clearLabel="Close inspector">
        <NodeBody
          node={node}
          graph={graph}
          health={healthByRepo?.get(node.repo) ?? null}
          role={roleByNodeId?.get(node.id) ?? null}
          onSelectNode={onSelectNode}
        />
      </SystemMapRailPanel>
    );
  }

  const edge = graph.edges.find((e) => e.id === selection.id);
  if (!edge) return null;
  return (
    <SystemMapRailPanel eyebrow="Relationship" onClear={onClose} clearLabel="Close inspector">
      <EdgeBody
        edge={edge}
        graph={graph}
        onSelectNode={onSelectNode}
        {...(onOpenContract ? { onOpenContract } : {})}
      />
    </SystemMapRailPanel>
  );
}
