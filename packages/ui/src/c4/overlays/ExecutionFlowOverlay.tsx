"use client";

import { useState } from "react";
import { Badge } from "../panels/panel-atoms";

export interface ExecutionFlowEntry {
  id: string;
  entry_point: string;
  score: number;
  call_chain: string[];
  crosses_community: boolean;
}

interface ExecutionFlowOverlayProps {
  flows: ExecutionFlowEntry[];
  visible: boolean;
}

export function ExecutionFlowOverlay({ flows, visible }: ExecutionFlowOverlayProps) {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  if (!visible) return null;

  const toggleExpanded = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  if (flows.length === 0) {
    return (
      <div
        style={{
          position: "fixed",
          right: 16,
          top: 80,
          width: 340,
          maxHeight: "70vh",
          overflowY: "auto",
          background: "rgba(15, 23, 42, 0.95)",
          border: "1px solid rgba(148, 163, 184, 0.2)",
          borderRadius: 8,
          padding: 16,
          color: "#e2e8f0",
          fontSize: 13,
          zIndex: 500,
        }}
      >
        No execution flows available
      </div>
    );
  }

  return (
    <div
      style={{
        position: "fixed",
        right: 16,
        top: 80,
        width: 340,
        maxHeight: "70vh",
        overflowY: "auto",
        background: "rgba(15, 23, 42, 0.95)",
        border: "1px solid rgba(148, 163, 184, 0.2)",
        borderRadius: 8,
        padding: 16,
        color: "#e2e8f0",
        fontSize: 13,
        zIndex: 500,
      }}
    >
      <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 12 }}>Execution Flows</div>
      {flows.map((flow) => {
        const isExpanded = expandedIds.has(flow.id);
        return (
          <div
            key={flow.id}
            style={{
              padding: "8px 0",
              borderBottom: "1px solid rgba(148, 163, 184, 0.12)",
            }}
          >
            <div
              onClick={() => toggleExpanded(flow.id)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                cursor: "pointer",
              }}
            >
              <span style={{ fontSize: 12 }}>▶</span>
              <span style={{ flex: 1, fontWeight: 500 }}>{flow.entry_point}</span>
              <Badge label={flow.score.toFixed(2)} />
            </div>
            {flow.crosses_community && (
              <div style={{ color: "#fbbf24", fontSize: 11, marginTop: 4, marginLeft: 20 }}>
                ⚠ Cross-boundary
              </div>
            )}
            {isExpanded && (
              <div style={{ marginTop: 6, marginLeft: 20 }}>
                {flow.call_chain.map((nodeId, idx) => (
                  <div
                    key={`${flow.id}-${idx}`}
                    style={{
                      fontSize: 11,
                      padding: "2px 0",
                      opacity: 0.85,
                    }}
                  >
                    <span style={{ color: "#94a3b8", marginRight: 6 }}>{idx + 1}.</span>
                    {nodeId}
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
