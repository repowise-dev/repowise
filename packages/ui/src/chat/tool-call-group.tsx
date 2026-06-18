"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Sparkles } from "lucide-react";
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
 */
export function ToolCallGroup({ toolCalls, onViewArtifact }: ToolCallGroupProps) {
  const running = toolCalls.some((tc) => tc.status === "running");
  const [expanded, setExpanded] = useState(false);

  if (toolCalls.length === 0) return null;

  // A lone tool call doesn't need a group wrapper.
  if (toolCalls.length === 1) {
    const tc = toolCalls[0]!;
    const artifact = tc.artifact;
    const handler = artifact && onViewArtifact ? () => onViewArtifact(artifact) : undefined;
    return <ToolCallBlock toolCall={tc} {...(handler ? { onViewArtifact: handler } : {})} />;
  }

  const open = expanded || running;

  return (
    <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] text-xs overflow-hidden">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-[var(--color-bg-overlay)] transition-colors"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={open}
      >
        {running ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--color-accent-primary)] shrink-0" />
        ) : (
          <Sparkles className="h-3.5 w-3.5 text-[var(--color-text-tertiary)] shrink-0" />
        )}
        <span className="font-medium text-[var(--color-text-secondary)]">
          {running ? "Working" : "Thinking"}
        </span>
        <span className="text-[var(--color-text-tertiary)]">
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
        <div className="border-t border-[var(--color-border-default)] p-1.5 space-y-1">
          {toolCalls.map((tc) => {
            const artifact = tc.artifact;
            const handler =
              artifact && onViewArtifact ? () => onViewArtifact(artifact) : undefined;
            return (
              <ToolCallBlock
                key={tc.id}
                toolCall={tc}
                {...(handler ? { onViewArtifact: handler } : {})}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
