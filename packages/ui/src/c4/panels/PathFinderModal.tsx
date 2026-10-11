"use client";

import { useMemo, useState } from "react";
import { useArchitectureStore } from "../store/use-architecture-store";
import { findShortestPath } from "../utils/graph-algorithms";
import { getTone } from "../../graph-primitives/tone-styles";
import { Badge } from "./panel-atoms";
import type { ArchNode } from "../types";

/** Typeahead node picker — replaces the unscalable <select> over all nodes. */
function NodeCombobox({
  label,
  nodes,
  value,
  onSelect,
}: {
  label: string;
  nodes: ArchNode[];
  value: string;
  onSelect: (nodeId: string) => void;
}) {
  const selected = useMemo(
    () => nodes.find((n) => n.id === value) ?? null,
    [nodes, value],
  );
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return nodes.slice(0, 20);
    return nodes
      .filter(
        (n) =>
          n.name.toLowerCase().includes(q) || n.id.toLowerCase().includes(q),
      )
      .slice(0, 20);
  }, [nodes, query]);

  return (
    <div style={{ marginBottom: 12, position: "relative" }}>
      <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 4 }}>{label}</div>
      <input
        type="text"
        value={open ? query : (selected?.name ?? "")}
        placeholder="Type to search nodes…"
        aria-label={`${label} node`}
        onFocus={() => {
          setOpen(true);
          setQuery("");
        }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={(e) => setQuery(e.target.value)}
        style={{
          width: "100%",
          padding: "8px 12px",
          background: "var(--color-bg-surface)",
          color: "var(--color-text-primary)",
          border: "1px solid var(--color-border-default)",
          borderRadius: 6,
          fontSize: 13,
          outline: "none",
        }}
      />
      {open && (
        <div
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            right: 0,
            marginTop: 4,
            maxHeight: 200,
            overflowY: "auto",
            background: "var(--color-bg-elevated)",
            border: "1px solid var(--color-border-default)",
            borderRadius: 6,
            zIndex: 10,
            boxShadow: "0 8px 24px rgba(0,0,0,0.25)",
          }}
        >
          {matches.length === 0 ? (
            <div style={{ padding: "8px 12px", fontSize: 12, opacity: 0.6 }}>
              No matching nodes
            </div>
          ) : (
            matches.map((n) => {
              const tone = getTone(n.node_type);
              return (
                <button
                  key={n.id}
                  type="button"
                  onMouseDown={(e) => {
                    e.preventDefault();
                    onSelect(n.id);
                    setOpen(false);
                  }}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    width: "100%",
                    textAlign: "left",
                    padding: "7px 12px",
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    color: "var(--color-text-primary)",
                    fontSize: 13,
                  }}
                >
                  <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {n.name}
                  </span>
                  <Badge label={n.node_type} color={tone.text} bg={tone.bg} />
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

export function PathFinderModal() {
  const view = useArchitectureStore((s) => s.view);
  const nodesById = useArchitectureStore((s) => s.nodesById);
  const pathFinderOpen = useArchitectureStore((s) => s.pathFinderOpen);
  const setPathFinderOpen = useArchitectureStore((s) => s.setPathFinderOpen);
  const selectNode = useArchitectureStore((s) => s.selectNode);

  const [fromId, setFromId] = useState("");
  const [toId, setToId] = useState("");
  const [result, setResult] = useState<string[] | null | undefined>(undefined);

  if (!pathFinderOpen) return null;

  const nodes = view?.nodes ?? [];
  const edges = view?.edges ?? [];

  const handleFind = () => {
    if (!fromId || !toId) return;
    const path = findShortestPath(edges, fromId, toId);
    setResult(path);
  };

  const handleClose = () => {
    setPathFinderOpen(false);
  };

  const handleNodeClick = (nodeId: string) => {
    selectNode(nodeId);
    setPathFinderOpen(false);
  };

  return (
    <div
      onClick={handleClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.5)",
        backdropFilter: "blur(4px)",
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 500,
          maxHeight: "80vh",
          overflowY: "auto",
          background: "var(--color-bg-elevated)",
          border: "1px solid var(--color-border-default)",
          borderRadius: 12,
          padding: 24,
          color: "var(--color-text-primary)",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 20,
          }}
        >
          <div style={{ fontWeight: 600, fontSize: 16 }}>Path Finder</div>
          <button
            type="button"
            onClick={handleClose}
            aria-label="Close path finder"
            style={{
              background: "none",
              border: "none",
              color: "var(--color-text-secondary)",
              cursor: "pointer",
              fontSize: 18,
              padding: 4,
            }}
          >
            ✕
          </button>
        </div>

        <NodeCombobox
          label="From"
          nodes={nodes}
          value={fromId}
          onSelect={(id) => {
            setFromId(id);
            setResult(undefined);
          }}
        />
        <NodeCombobox
          label="To"
          nodes={nodes}
          value={toId}
          onSelect={(id) => {
            setToId(id);
            setResult(undefined);
          }}
        />

        <button
          type="button"
          onClick={handleFind}
          disabled={!fromId || !toId}
          style={{
            width: "100%",
            padding: "10px 0",
            background: fromId && toId ? "var(--color-accent-fill)" : "var(--color-bg-wash-hover)",
            color: fromId && toId ? "var(--color-text-on-accent)" : "var(--color-text-secondary)",
            border: "none",
            borderRadius: 6,
            fontSize: 13,
            fontWeight: 600,
            cursor: fromId && toId ? "pointer" : "not-allowed",
            marginBottom: 16,
          }}
        >
          Find Path
        </button>

        {result !== undefined && (
          <div>
            {result === null ? (
              <div style={{ fontSize: 12, opacity: 0.6, textAlign: "center", padding: 12 }}>
                No path found between these nodes
              </div>
            ) : (
              <div>
                {result.map((nodeId, idx) => {
                  const node = nodesById.get(nodeId);
                  const tone = node ? getTone(node.node_type) : getTone("external");
                  return (
                    <div
                      key={nodeId}
                      onClick={() => handleNodeClick(nodeId)}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "8px 10px",
                        borderRadius: 6,
                        cursor: "pointer",
                        borderBottom: "1px solid var(--color-border-subtle)",
                      }}
                    >
                      <span style={{ fontSize: 12, color: "var(--color-text-secondary)", minWidth: 20 }}>
                        {idx + 1}.
                      </span>
                      <span style={{ flex: 1, fontSize: 13 }}>
                        {node?.name ?? nodeId}
                      </span>
                      {node && (
                        <Badge
                          label={node.node_type}
                          color={tone.text}
                          bg={tone.bg}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
