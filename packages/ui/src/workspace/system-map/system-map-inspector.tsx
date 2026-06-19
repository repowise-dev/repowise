"use client";

/**
 * Right-rail inspector for the Live System Map. Mirrors the C4 node inspector
 * idiom (absolute panel, top-right, close button) but is driven by the current
 * selection: a service node (health, provider/consumer counts, contract types,
 * connected services) or an edge (kind, match type, confidence, weight, and the
 * underlying contract refs with a hook to open them on the Contracts surface).
 */

import { X } from "lucide-react";
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
import type { RepoHealth, SystemMapSelection } from "./types";

export interface SystemMapInspectorProps {
  selection: SystemMapSelection;
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

const panelStyle: React.CSSProperties = {
  position: "absolute",
  top: 12,
  right: 12,
  width: 300,
  maxHeight: "calc(100% - 24px)",
  overflowY: "auto",
  background: "var(--color-bg-elevated)",
  border: "1px solid var(--color-border-default)",
  borderRadius: 10,
  boxShadow: "0 8px 24px rgba(0,0,0,0.28)",
  zIndex: 4,
  fontSize: 12,
  color: "var(--color-text-secondary)",
};

function Header({ kind, onClose }: { kind: string; onClose: () => void }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "10px 12px",
        borderBottom: "1px solid var(--color-border-default)",
      }}
    >
      <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase", color: "var(--color-text-tertiary)" }}>
        {kind}
      </span>
      <button type="button" onClick={onClose} aria-label="Close inspector" style={{ cursor: "pointer", color: "var(--color-text-tertiary)", display: "inline-flex" }}>
        <X size={14} />
      </button>
    </div>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "3px 0" }}>
      <span style={{ color: "var(--color-text-tertiary)" }}>{label}</span>
      <span style={{ color: "var(--color-text-primary)", textAlign: "right", wordBreak: "break-word" }}>{value}</span>
    </div>
  );
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
    <div style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        {health && <HealthRing score={health.score} source={health.source} size={36} />}
        <div>
          <div style={{ fontSize: 14, fontWeight: 600, color: "var(--color-text-primary)" }}>{node.name}</div>
          <div style={{ fontSize: 11, color: "var(--color-text-tertiary)" }}>{kind.label} · {node.repo}</div>
        </div>
      </div>
      <div>
        {role && (
          <Field
            label="Role"
            value={
              <span title={roleStyle(role.role).description} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                <span style={{ width: 7, height: 7, borderRadius: "50%", background: roleStyle(role.role).color }} />
                {roleStyle(role.role).label}
              </span>
            }
          />
        )}
        {role && (
          <Field
            label="Visibility (in / out)"
            value={`${role.visibility_fan_in} / ${role.visibility_fan_out}`}
          />
        )}
        {node.service_path && <Field label="Path" value={<span style={{ fontFamily: "var(--font-mono, monospace)", fontSize: 11 }}>{node.service_path}</span>} />}
        <Field label="Provides" value={`${node.provider_count} contracts`} />
        <Field label="Consumes" value={`${node.consumer_count} contracts`} />
        <Field label="Types" value={node.contract_types.length ? node.contract_types.join(", ") : "none"} />
        {node.is_orphan_provider && <Field label="Status" value={<span style={{ color: "var(--color-warning)" }}>Orphan provider</span>} />}
        {node.is_orphan_consumer && <Field label="Status" value={<span style={{ color: "var(--color-warning)" }}>Orphan consumer</span>} />}
        {node.is_isolated && <Field label="Status" value={<span style={{ color: "var(--color-text-tertiary)" }}>Isolated</span>} />}
      </div>
      <NeighbourList title={`Depends on (${outgoing.length})`} edges={outgoing} resolve={(e) => e.target} neighbour={neighbour} onSelectNode={onSelectNode} />
      <NeighbourList title={`Depended on by (${incoming.length})`} edges={incoming} resolve={(e) => e.source} neighbour={neighbour} onSelectNode={onSelectNode} />
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
      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase", color: "var(--color-text-tertiary)", marginBottom: 3 }}>
        {title}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {edges.map((e) => {
          const id = resolve(e);
          const s = edgeKindStyle(e.kind);
          return (
            <button
              key={e.id}
              type="button"
              onClick={() => onSelectNode(id)}
              style={{ display: "flex", alignItems: "center", gap: 6, padding: "2px 4px", borderRadius: 4, cursor: "pointer", textAlign: "left", color: "var(--color-text-secondary)" }}
            >
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: s.color, flexShrink: 0 }} />
              <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{neighbour(id)}</span>
              <span style={{ fontSize: 10, color: "var(--color-text-tertiary)" }}>{s.label}</span>
            </button>
          );
        })}
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
    <div style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--color-text-primary)" }}>
        <Icon size={13} style={{ color: s.color }} aria-hidden />
        {s.label} relationship
      </div>
      <div>
        <Field label="From" value={<LinkText label={name(edge.source)} onClick={() => onSelectNode(edge.source)} />} />
        <Field label="To" value={<LinkText label={name(edge.target)} onClick={() => onSelectNode(edge.target)} />} />
        <Field label="Match" value={matchTypeLabel(edge.match_type)} />
        <Field label="Confidence" value={`${Math.round(edge.confidence * 100)}%`} />
        <Field label="Weight" value={String(edge.weight)} />
        <Field label="Nature" value={edge.structural ? "Structural" : "Behavioral"} />
      </div>
      {edge.contract_refs.length > 0 && (
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase", color: "var(--color-text-tertiary)", marginBottom: 3 }}>
            Evidence ({edge.contract_refs.length})
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {edge.contract_refs.map((ref) => (
              <button
                key={ref}
                type="button"
                onClick={onOpenContract ? () => onOpenContract(ref) : undefined}
                disabled={!onOpenContract}
                title={onOpenContract ? "Open on the Contracts page" : ref}
                style={{
                  fontFamily: "var(--font-mono, monospace)",
                  fontSize: 10.5,
                  textAlign: "left",
                  color: onOpenContract ? "var(--color-accent-primary)" : "var(--color-text-tertiary)",
                  cursor: onOpenContract ? "pointer" : "default",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {ref}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function LinkText({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} style={{ color: "var(--color-accent-primary)", cursor: "pointer" }}>
      {label}
    </button>
  );
}

export function SystemMapInspector({ selection, graph, healthByRepo, roleByNodeId, onClose, onSelectNode, onOpenContract }: SystemMapInspectorProps) {
  if (!selection) return null;

  if (selection.type === "node") {
    const node = graph.nodes.find((n) => n.id === selection.id);
    if (!node) return null;
    return (
      <div style={panelStyle}>
        <Header kind="Service" onClose={onClose} />
        <NodeBody node={node} graph={graph} health={healthByRepo?.get(node.repo) ?? null} role={roleByNodeId?.get(node.id) ?? null} onSelectNode={onSelectNode} />
      </div>
    );
  }

  const edge = graph.edges.find((e) => e.id === selection.id);
  if (!edge) return null;
  return (
    <div style={panelStyle}>
      <Header kind="Relationship" onClose={onClose} />
      <EdgeBody edge={edge} graph={graph} onSelectNode={onSelectNode} {...(onOpenContract ? { onOpenContract } : {})} />
    </div>
  );
}
