"use client";

import { X, Code, MapPin, Layers, ExternalLink, Folder, CornerDownRight } from "lucide-react";
import { useArchitectureStore } from "../store/use-architecture-store";
import { getTone } from "../../graph-primitives/tone-styles";
import { THEME } from "../theme/theme-variables";
import { HealthScoreRing } from "../../dashboard/health-score-ring";
import { Section, Title, Sub, KVList, ActionRow, ActionButton, Badge, Pill } from "./panel-atoms";

export interface ArchNodeHealth {
  health_score: number;
  hotspot_count: number;
  dead_code_count: number;
  doc_coverage_pct: number;
  contributor_count?: number | undefined;
  is_silo?: boolean | undefined;
}

export interface ArchNodeInfoProps {
  health?: ArchNodeHealth | null | undefined;
  contributors?: { name: string; files: number; pct?: number }[] | undefined;
  renderDoc?: ((content: string) => React.ReactNode) | undefined;
  docContent?: string | null | undefined;
  onOpenInGraph?: ((path: string) => void) | undefined;
  onOpenDoc?: ((href: string) => void) | undefined;
}

export function ArchNodeInfo(props: ArchNodeInfoProps) {
  const selectedNodeId = useArchitectureStore((s) => s.selectedNodeId);
  const nodesById = useArchitectureStore((s) => s.nodesById);
  const edgesBySource = useArchitectureStore((s) => s.edgesBySource);
  const edgesByTarget = useArchitectureStore((s) => s.edgesByTarget);
  const nodeIdToLayerId = useArchitectureStore((s) => s.nodeIdToLayerId);
  const selectNode = useArchitectureStore((s) => s.selectNode);
  const drillIntoLayer = useArchitectureStore((s) => s.drillIntoLayer);
  const drillIntoSubGroup = useArchitectureStore((s) => s.drillIntoSubGroup);
  const openCodeViewer = useArchitectureStore((s) => s.openCodeViewer);
  const setFocusNode = useArchitectureStore((s) => s.setFocusNode);
  const view = useArchitectureStore((s) => s.view);

  const node = selectedNodeId ? nodesById.get(selectedNodeId) ?? null : null;

  // Scope selection (kg-ux plan B5): single-clicking a layer/group card
  // selects it — this panel is the "inspect" half of the grammar, with the
  // explicit drill affordance ("Open on canvas") as the other half.
  if (!node && selectedNodeId && view) {
    const layerScope = view.layers.find((l) => l.id === selectedNodeId);
    if (layerScope) {
      return (
        <ScopeInfo
          kind="layer"
          name={layerScope.name}
          description={layerScope.description}
          fileCount={layerScope.file_count}
          groupCount={layerScope.sub_groups.length}
          healthScore={layerScope.health_score}
          onClose={() => selectNode(null)}
          onOpen={() => drillIntoLayer(layerScope.id)}
        />
      );
    }
    for (const l of view.layers) {
      const group = l.sub_groups.find((g) => g.id === selectedNodeId);
      if (group) {
        return (
          <ScopeInfo
            kind="group"
            name={group.name}
            description={`Sub-group of ${l.name}`}
            fileCount={group.node_ids.length}
            onClose={() => selectNode(null)}
            onOpen={() => drillIntoSubGroup(group.id)}
          />
        );
      }
    }

    // Folder containers (dir:<path>) — the boxes the detail canvas groups
    // files into. They aren't ArchNodes, so without this branch selecting
    // one left the inspector blank.
    if (selectedNodeId.startsWith("dir:")) {
      const path = selectedNodeId.slice(4);
      const memberCount = view.nodes.filter((n) => n.id.startsWith(`${path}/`)).length;
      return (
        <ScopeInfo
          kind="folder"
          name={path.split("/").pop() ?? path}
          description={path}
          fileCount={memberCount}
          onClose={() => selectNode(null)}
          onOpen={() => useArchitectureStore.getState().toggleContainer(selectedNodeId)}
        />
      );
    }

    // The "+N more" card that absorbs files beyond the visible-box budget.
    if (selectedNodeId === "container:__overflow") {
      return (
        <ScopeInfo
          kind="folder"
          name="More files"
          description="Files beyond the visible-box budget for this scope. Expand the card to lay them out."
          onClose={() => selectNode(null)}
          onOpen={() => useArchitectureStore.getState().toggleContainer(selectedNodeId)}
        />
      );
    }

    // Portal chips (portal:layer:A→layer:B) — wayfinding to a neighbouring
    // layer this scope exchanges imports with.
    if (selectedNodeId.startsWith("portal:")) {
      const targetLayerId = selectedNodeId.split("→")[1];
      const targetLayer = targetLayerId
        ? view.layers.find((l) => l.id === targetLayerId)
        : undefined;
      if (targetLayer) {
        return (
          <ScopeInfo
            kind="portal"
            name={targetLayer.name}
            description={`Files in this scope exchange imports with the ${targetLayer.name} layer. Open it to follow them.`}
            fileCount={targetLayer.file_count}
            onClose={() => selectNode(null)}
            onOpen={() => drillIntoLayer(targetLayer.id)}
          />
        );
      }
    }
  }

  if (!node) return null;

  const tone = getTone(node.node_type);
  const layerId = nodeIdToLayerId.get(node.id);
  const layer = layerId && view ? view.layers.find((l) => l.id === layerId) : undefined;

  const incoming = edgesByTarget.get(node.id) ?? [];
  const outgoing = edgesBySource.get(node.id) ?? [];

  return (
    <>
      <HeaderSection
        node={node}
        tone={tone}
        onClose={() => selectNode(null)}
      />

      <Section>
        <Sub>{node.summary}</Sub>
      </Section>

      {node.file_path && (
        <Section>
          <div style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", fontSize: 11, wordBreak: "break-all" }}>
            {node.file_path}
            {node.line_range && (
              <span style={{ opacity: 0.6 }}>{" "}L{node.line_range[0]}–L{node.line_range[1]}</span>
            )}
          </div>
        </Section>
      )}

      <Section>
        <ActionRow>
          {node.file_path && (
            <ActionButton onClick={() => openCodeViewer(node.id)} icon={Code}>
              Open code
            </ActionButton>
          )}
          <ActionButton onClick={() => setFocusNode(node.id)} variant="ghost" icon={MapPin}>
            Focus neighborhood
          </ActionButton>
          {layerId && layer && (
            <ActionButton onClick={() => drillIntoLayer(layerId)} variant="ghost" icon={Layers}>
              {layer.name}
            </ActionButton>
          )}
        </ActionRow>
      </Section>

      {props.docContent && props.renderDoc ? (
        <Section title="Wiki">
          <div style={{ fontSize: 12, lineHeight: 1.45, color: "var(--color-text-secondary)" }}>
            {props.renderDoc(props.docContent)}
          </div>
          {props.onOpenDoc && (
            <div style={{ marginTop: 6 }}>
              <ActionButton onClick={() => props.onOpenDoc!("")} variant="ghost" icon={ExternalLink}>
                Open full page
              </ActionButton>
            </div>
          )}
        </Section>
      ) : (
        <Section title="Wiki">
          <div style={{ fontSize: 11, color: "var(--color-text-secondary)", opacity: 0.7 }}>
            Wiki not generated yet. Run docs generation to populate this section.
          </div>
        </Section>
      )}

      {props.health && (
        <HealthSection
          health={props.health}
          node={node}
        />
      )}

      <OwnershipSection
        primaryOwner={node.primary_owner}
        primaryOwnerPct={node.primary_owner_pct}
        busFactor={node.bus_factor}
      />

      {props.contributors && props.contributors.length > 0 && (
        <Section title="Contributors">
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {props.contributors.slice(0, 5).map((c) => (
              <li
                key={c.name}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  padding: "3px 0",
                  borderTop: "1px solid var(--color-border-subtle)",
                  fontSize: 11,
                }}
              >
                <span style={{ opacity: 0.9 }}>{c.name}</span>
                <span style={{ opacity: 0.6 }}>
                  {c.files} files
                  {c.pct != null && ` · ${Math.round(c.pct * 100)}%`}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <ConnectionsSection
        incoming={incoming}
        outgoing={outgoing}
        nodesById={nodesById}
        onSelect={selectNode}
      />

      {node.tags.length > 0 && (
        <Section title="Tags">
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {node.tags.map((tag) => (
              <Pill key={tag} label={tag} />
            ))}
          </div>
        </Section>
      )}

      <Section title="Graph metrics">
        <KVList
          rows={[
            ["PageRank", `top ${Math.round(100 - node.pagerank_percentile)}%`],
            ["Betweenness", node.betweenness.toFixed(2)],
            ["In-degree", String(node.in_degree)],
            ["Out-degree", String(node.out_degree)],
          ]}
        />
      </Section>
    </>
  );
}

function HeaderSection({
  node,
  tone,
  onClose,
}: {
  node: { node_type: string; name: string; complexity: string };
  tone: { band: string; text: string };
  onClose: () => void;
}) {
  const dotColor = THEME.complexity[node.complexity] ?? "var(--color-text-muted)";

  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "10px 12px",
        borderBottom: "1px solid var(--color-border-default)",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, flex: 1, minWidth: 0 }}>
        <Badge label={node.node_type} color={tone.text} bg={tone.band} />
        <span
          aria-label={`Complexity: ${node.complexity}`}
          style={{
            display: "inline-block",
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: dotColor,
            flexShrink: 0,
          }}
        />
        <Title style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {node.name}
        </Title>
      </div>
      <button
        type="button"
        aria-label="Close panel"
        onClick={onClose}
        style={{ background: "none", border: "none", cursor: "pointer", color: "inherit", padding: 2, flexShrink: 0 }}
      >
        <X size={14} />
      </button>
    </header>
  );
}

function HealthSection({
  health,
  node,
}: {
  health: ArchNodeHealth;
  node: { primary_owner: string | null; bus_factor: number | null };
}) {
  return (
    <Section title="Health">
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
        <HealthScoreRing score={health.health_score} size={60} />
        <KVList
          rows={[
            ["Hotspots", String(health.hotspot_count)],
            ["Dead code", String(health.dead_code_count)],
            ["Doc coverage", `${Math.round(health.doc_coverage_pct)}%`],
          ]}
        />
      </div>
      {health.is_silo && (
        <div style={{ marginTop: 6, fontSize: 10, color: "var(--color-warning)", fontWeight: 600 }}>
          ⚠ Knowledge silo detected
        </div>
      )}
    </Section>
  );
}

function OwnershipSection({
  primaryOwner,
  primaryOwnerPct,
  busFactor,
}: {
  primaryOwner: string | null;
  primaryOwnerPct: number | null;
  busFactor: number | null;
}) {
  if (!primaryOwner && busFactor == null) return null;

  const rows: [string, string][] = [];
  if (primaryOwner) {
    rows.push(["Primary owner", `${primaryOwner}${primaryOwnerPct != null ? ` (${Math.round(primaryOwnerPct * 100)}%)` : ""}`]);
  }
  if (busFactor != null) {
    rows.push(["Bus factor", String(busFactor)]);
  }

  return (
    <Section title="Ownership">
      <KVList rows={rows} />
    </Section>
  );
}

function ConnectionsSection({
  incoming,
  outgoing,
  nodesById,
  onSelect,
}: {
  incoming: { source: string; edge_type: string }[];
  outgoing: { target: string; edge_type: string }[];
  nodesById: Map<string, { name: string }>;
  onSelect: (id: string) => void;
}) {
  if (incoming.length === 0 && outgoing.length === 0) return null;

  return (
    <>
      {incoming.length > 0 && (
        <Section title={`Incoming (${incoming.length})`}>
          <ConnectionList
            edges={incoming.map((e) => ({ nodeId: e.source, edgeType: e.edge_type }))}
            nodesById={nodesById}
            onSelect={onSelect}
            max={10}
          />
        </Section>
      )}
      {outgoing.length > 0 && (
        <Section title={`Outgoing (${outgoing.length})`}>
          <ConnectionList
            edges={outgoing.map((e) => ({ nodeId: e.target, edgeType: e.edge_type }))}
            nodesById={nodesById}
            onSelect={onSelect}
            max={10}
          />
        </Section>
      )}
    </>
  );
}

function ConnectionList({
  edges,
  nodesById,
  onSelect,
  max,
}: {
  edges: { nodeId: string; edgeType: string }[];
  nodesById: Map<string, { name: string }>;
  onSelect: (id: string) => void;
  max: number;
}) {
  const shown = edges.slice(0, max);
  const remaining = edges.length - max;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
      {shown.map((e, i) => {
        const connectedNode = nodesById.get(e.nodeId);
        return (
          <button
            key={`${e.nodeId}-${e.edgeType}-${i}`}
            type="button"
            aria-label={`Select ${connectedNode?.name ?? e.nodeId}`}
            onClick={() => onSelect(e.nodeId)}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "3px 6px",
              background: "none",
              border: "none",
              borderRadius: 4,
              cursor: "pointer",
              color: "var(--color-text-primary)",
              fontSize: 11,
              textAlign: "left",
            }}
            onMouseEnter={(e) => { (e.currentTarget.style.background = "var(--color-bg-wash-hover)"); }}
            onMouseLeave={(e) => { (e.currentTarget.style.background = "none"); }}
          >
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {connectedNode?.name ?? e.nodeId}
            </span>
            <span style={{ opacity: 0.5, fontSize: 10, flexShrink: 0, marginLeft: 6 }}>{e.edgeType}</span>
          </button>
        );
      })}
      {remaining > 0 && (
        <Sub style={{ paddingLeft: 6 }}>+{remaining} more</Sub>
      )}
    </div>
  );
}

/** Inspect card for a selected layer / sub-group (kg-ux plan B5): summary +
 * stats + the explicit drill button mirroring double-click on the canvas. */
function ScopeInfo({
  kind,
  name,
  description,
  fileCount,
  groupCount,
  healthScore,
  onClose,
  onOpen,
}: {
  kind: "layer" | "group" | "folder" | "portal";
  name: string;
  description?: string | undefined;
  fileCount?: number | undefined;
  groupCount?: number | undefined;
  healthScore?: number | null | undefined;
  onClose: () => void;
  onOpen: () => void;
}) {
  const Icon = kind === "layer" ? Layers : kind === "portal" ? ExternalLink : Folder;
  const rows: [string, string][] = fileCount != null ? [["Files", String(fileCount)]] : [];
  if (kind === "layer" && groupCount && groupCount > 0) {
    rows.push(["Groups", String(groupCount)]);
  }
  if (healthScore !== null && healthScore !== undefined) {
    rows.push(["Health", String(Math.round(healthScore))]);
  }

  return (
    <>
      <Section>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
          <Icon size={16} style={{ flexShrink: 0, marginTop: 2, color: "var(--color-accent-primary)" }} aria-hidden />
          <div style={{ flex: 1, minWidth: 0 }}>
            <Title>{name}</Title>
            <Sub style={{ marginTop: 2 }}>
              {kind === "layer"
                ? "Layer"
                : kind === "folder"
                  ? "Folder"
                  : kind === "portal"
                    ? "Connected layer"
                    : "Group"}
            </Sub>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close inspector"
            style={{
              background: "none",
              border: "none",
              color: "var(--color-text-secondary)",
              cursor: "pointer",
              padding: 2,
            }}
          >
            <X size={14} aria-hidden />
          </button>
        </div>
        {description && <Sub style={{ marginTop: 6 }}>{description}</Sub>}
      </Section>

      {rows.length > 0 && (
        <Section>
          <KVList rows={rows} />
        </Section>
      )}

      <Section>
        <ActionRow>
          <ActionButton onClick={onOpen} icon={CornerDownRight} variant="primary">
            {kind === "layer" || kind === "portal"
              ? "Open layer"
              : kind === "folder"
                ? "Expand on canvas"
                : "Open group"}
          </ActionButton>
        </ActionRow>
        <Sub style={{ marginTop: 6, fontSize: 10 }}>
          Double-clicking the card does the same. Esc deselects.
        </Sub>
      </Section>
    </>
  );
}
