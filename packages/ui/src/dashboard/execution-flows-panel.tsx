"use client";

import { useState } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";
import { Badge } from "../ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { cn } from "../lib/cn";
import type { ExecutionFlowEntry } from "@repowise/types/graph";

interface ExecutionFlowsPanelProps {
  flows: ExecutionFlowEntry[];
  repoId: string;
}

function FlowRow({ flow }: { flow: ExecutionFlowEntry }) {
  const [expanded, setExpanded] = useState(false);

  const scoreColor =
    flow.entry_point_score >= 0.7
      ? "text-green-400 bg-green-400/10 border-green-400/20"
      : flow.entry_point_score >= 0.4
        ? "text-yellow-400 bg-yellow-400/10 border-yellow-400/20"
        : "text-[var(--color-text-tertiary)] bg-[var(--color-bg-elevated)] border-[var(--color-border-default)]";

  return (
    <div className="border-b border-[var(--color-border-default)] last:border-0">
      <button
        onClick={() => setExpanded((s) => !s)}
        className="flex items-center gap-2 w-full px-3 py-2.5 text-left hover:bg-[var(--color-bg-elevated)] transition-colors"
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />
        )}

        <span className="text-xs font-mono text-[var(--color-text-primary)] truncate min-w-0 flex-1">
          {flow.entry_point_name}
        </span>

        <span className={cn("text-[10px] font-mono px-1.5 py-0.5 rounded border shrink-0", scoreColor)}>
          {(flow.entry_point_score * 100).toFixed(0)}
        </span>

        <span className="text-[10px] text-[var(--color-text-tertiary)] shrink-0 tabular-nums">
          {flow.depth} calls
        </span>

        {flow.crosses_community && (
          <Badge variant="outline" className="text-[9px] shrink-0 h-4">
            cross-community
          </Badge>
        )}
      </button>

      {expanded && flow.trace.length > 1 && (
        <div className="px-3 pb-3 pl-8">
          <div className="flex flex-wrap items-center gap-1">
            {flow.trace.slice(0, 12).map((sym, i) => {
              const name = sym.includes("::") ? sym.split("::").pop() : sym.split("/").pop();
              return (
                <span key={i} className="flex items-center gap-1">
                  {i > 0 && (
                    <ChevronRight className="h-2.5 w-2.5 text-[var(--color-text-tertiary)]" />
                  )}
                  <span
                    className="text-[10px] font-mono text-[var(--color-text-secondary)] px-1 py-0.5 rounded bg-[var(--color-bg-elevated)]"
                    title={sym}
                  >
                    {name}
                  </span>
                </span>
              );
            })}
            {flow.trace.length > 12 && (
              <span className="text-[10px] text-[var(--color-text-tertiary)]">
                +{flow.trace.length - 12} more
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function ExecutionFlowsPanel({ flows, repoId: _repoId }: ExecutionFlowsPanelProps) {
  if (flows.length === 0) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Execution Flows</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div className="divide-y divide-[var(--color-border-default)]">
          {flows.slice(0, 8).map((flow) => (
            <FlowRow key={flow.entry_point} flow={flow} />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
