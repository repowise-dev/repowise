"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { ToolCallBlock } from "./tool-call-block";
import type { ChatUIToolCall } from "@repowise-dev/types/chat";

interface ToolCallGroupProps {
  toolCalls: ChatUIToolCall[];
  onViewArtifact?: (artifact: { type: string; data: Record<string, unknown> }) => void;
}

/**
 * Collapses a run of tool calls into a single "thinking" group instead of N
 * stacked bordered boxes. Auto-expands while any step is running so progress
 * stays visible; collapses to a one-line summary once the work is done.
 *
 * The group owns the one border and the one ground. Steps inside it are
 * hairline rows — nesting a bordered box inside a bordered box of the same
 * plane was the "box soup" the section style exists to remove, and it happened
 * once per step.
 */
export function ToolCallGroup({ toolCalls, onViewArtifact }: ToolCallGroupProps) {
  const running = toolCalls.some((tc) => tc.status === "running");
  const [expanded, setExpanded] = useState(false);

  if (toolCalls.length === 0) return null;

  const shell =
    "rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] text-xs overflow-hidden";

  // A lone tool call doesn't need a group wrapper, but it still needs the
  // container the row no longer carries itself.
  if (toolCalls.length === 1) {
    const tc = toolCalls[0]!;
    const artifact = tc.artifact;
    const handler = artifact && onViewArtifact ? () => onViewArtifact(artifact) : undefined;
    return (
      <div className={shell}>
        <ToolCallBlock toolCall={tc} {...(handler ? { onViewArtifact: handler } : {})} />
      </div>
    );
  }

  const open = expanded || running;

  return (
    <div className={shell}>
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={open}
      >
        {running && (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--color-accent-primary)] shrink-0" />
        )}
        <span className="font-medium text-[var(--color-text-secondary)]">
          {running ? "Working" : "Thinking"}
        </span>
        <span className="text-[var(--color-text-tertiary)] tabular-nums">
          · {toolCalls.length} steps
        </span>
        <span className="ml-auto shrink-0">
          {open ? (
            <ChevronDown className="h-3 w-3 text-[var(--color-text-tertiary)]" />
          ) : (
            <ChevronRight className="h-3 w-3 text-[var(--color-text-tertiary)]" />
          )}
        </span>
      </button>
      {open && (
        <div className="border-t border-[var(--color-border-default)]">
          {toolCalls.map((tc, idx) => {
            const artifact = tc.artifact;
            const handler =
              artifact && onViewArtifact ? () => onViewArtifact(artifact) : undefined;
            return (
              <ToolCallBlock
                key={tc.id}
                toolCall={tc}
                divided={idx > 0}
                {...(handler ? { onViewArtifact: handler } : {})}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
