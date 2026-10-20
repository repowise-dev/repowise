"use client";

import { useState, type CSSProperties } from "react";
import { ChevronRight, ChevronDown, Workflow } from "lucide-react";
import { Badge } from "../ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { EmptyState } from "../shared/empty-state";
import { isNameMatch, originDescriptor, terminationCopy } from "../graph/edge-provenance";
import type { ExecutionFlowEntry } from "@repowise-dev/types/graph";

interface ExecutionFlowsPanelProps {
  flows: ExecutionFlowEntry[];
  repoId: string;
  linkPrefix?: string;
}

function FlowRow({ flow }: { flow: ExecutionFlowEntry }) {
  const [expanded, setExpanded] = useState(false);
  const stop = terminationCopy(flow.termination);
  // Only the hops whose pills are actually drawn, so the key does not promise
  // a dash that sits behind the "+N more" cut.
  const shownVia = (flow.trace_via ?? []).slice(0, 11);

  const scoreStyle: CSSProperties =
    flow.entry_point_score >= 0.7
      ? {
          color: "var(--color-success)",
          background: "color-mix(in srgb, var(--color-success) 12%, transparent)",
          borderColor: "color-mix(in srgb, var(--color-success) 25%, transparent)",
        }
      : flow.entry_point_score >= 0.4
        ? {
            color: "var(--color-warning)",
            background: "color-mix(in srgb, var(--color-warning) 12%, transparent)",
            borderColor: "color-mix(in srgb, var(--color-warning) 25%, transparent)",
          }
        : {
            color: "var(--color-text-tertiary)",
            background: "var(--color-bg-elevated)",
            borderColor: "var(--color-border-default)",
          };

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

        <span
          className="text-[10px] font-mono px-1.5 py-0.5 rounded border shrink-0"
          style={scoreStyle}
        >
          {(flow.entry_point_score * 100).toFixed(0)}
        </span>

        <span className="text-[10px] text-[var(--color-text-tertiary)] shrink-0 tabular-nums">
          {flow.depth} calls
        </span>

        {flow.crosses_community && (
          <Badge variant="outline" className="text-[10px] shrink-0 h-4">
            cross-community
          </Badge>
        )}
      </button>

      {expanded && flow.trace.length > 1 && (
        <div className="px-3 pb-3 pl-8">
          <div className="flex flex-wrap items-center gap-y-2">
            {flow.trace.slice(0, 12).map((sym, i) => {
              const name = sym.includes("::") ? sym.split("::").pop() : sym.split("/").pop();
              // `trace_via` is pairwise, so the hop arriving at pill `i` is
              // `trace_via[i - 1]`. Dotted marks the hop we would not assert;
              // the title carries the reason, since a 12px rule cannot.
              const hop = originDescriptor(flow.trace_via?.[i - 1]);
              return (
                <span
                  key={i}
                  className="flex items-center"
                  {...(hop ? { title: `Reached because ${hop.because}` } : {})}
                >
                  {i > 0 &&
                    (hop?.tier === "name_match" ? (
                      <>
                        {/* The dash is the only visual carrier, so the word has
                            to exist for a reader who cannot see it. */}
                        <span className="sr-only">resolved by name match: </span>
                        <span
                          aria-hidden
                          className="w-3 shrink-0 border-t border-dashed"
                          style={{ borderColor: "var(--color-text-tertiary)" }}
                        />
                      </>
                    ) : (
                      <span
                        aria-hidden
                        className="h-px w-3 shrink-0"
                        style={{ background: "var(--color-border-hover)" }}
                      />
                    ))}
                  <span
                    className="rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-0.5 font-mono text-[10px] text-[var(--color-text-secondary)]"
                    title={sym}
                  >
                    {name}
                  </span>
                </span>
              );
            })}
            {flow.trace.length > 12 && (
              <span className="ml-1 text-[10px] text-[var(--color-text-tertiary)]">
                +{flow.trace.length - 12} more
              </span>
            )}
          </div>
          {/* A key, only when there is a dash to decode. The two sibling
              surfaces spell the tier as a word; a chain has no room per link,
              so the encoding is positional and needs naming once. */}
          {shownVia.some(isNameMatch) && (
            <p className="mt-2 flex items-center gap-1.5 text-[10px] text-[var(--color-text-tertiary)]">
              <span
                aria-hidden
                className="w-3 shrink-0 border-t border-dashed"
                style={{ borderColor: "var(--color-text-tertiary)" }}
              />
              resolved by name match
            </p>
          )}
          {stop && (
            <p className="mt-2 text-[10px] leading-snug text-[var(--color-text-tertiary)]">
              {stop.sentence}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export function ExecutionFlowsPanel({ flows, repoId, linkPrefix }: ExecutionFlowsPanelProps) {
  const prefix = linkPrefix ?? `/repos/${repoId}`;
  if (flows.length === 0) {
    return (
      <Card>
        <CardContent className="p-0">
          <EmptyState
            icon={<Workflow className="h-8 w-8" />}
            title="No execution flows"
            description="No execution flow data is available for this repository."
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium">Execution Flows</CardTitle>
          <a href={`${prefix}/architecture?view=communities`} className="text-[10px] text-[var(--color-accent-primary)] hover:underline">
            View in Graph →
          </a>
        </div>
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
