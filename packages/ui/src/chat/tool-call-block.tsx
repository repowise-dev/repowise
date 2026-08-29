"use client";

/**
 * One step of the model's work, as a hairline row.
 *
 * It used to be a bordered `bg-elevated` box that `ToolCallGroup` then nested
 * inside another bordered `bg-elevated` box — the same plane twice, with two
 * borders around it. The group owns the container now; a step is a row.
 *
 * A finished step carries no success badge. A green check on every completed
 * row says nothing, because success is the default and the failure case is what
 * needs marking. Only a *running* step shows a marker, and only while it runs.
 */

import { useState } from "react";
import { ChevronDown, ChevronRight, ArrowUpRight } from "lucide-react";
import { cn } from "../lib/cn";
import { WorkingOrb } from "./working-orb";
import type { ChatUIToolCall } from "@repowise-dev/types/chat";

const TOOL_LABELS: Record<string, string> = {
  get_overview: "Getting codebase overview",
  get_context: "Looking up context",
  get_risk: "Assessing risk",
  get_change_risk: "Scoring change risk",
  get_why: "Querying decisions",
  search_codebase: "Searching codebase",
  get_dead_code: "Checking dead code",
};

const MICRO_LABEL =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

interface ToolCallBlockProps {
  toolCall: ChatUIToolCall;
  onViewArtifact?: () => void;
  /** Renders the top hairline. Omitted on the first row of a group. */
  divided?: boolean;
}

export function ToolCallBlock({
  toolCall,
  onViewArtifact,
  divided = false,
}: ToolCallBlockProps) {
  const [expanded, setExpanded] = useState(false);
  const label = TOOL_LABELS[toolCall.name] ?? toolCall.name;
  const isRunning = toolCall.status === "running";
  const isError = toolCall.status === "error";

  return (
    <div
      className={cn(
        "text-xs",
        divided && "border-t border-[var(--color-border-default)]",
      )}
    >
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="flex flex-1 min-w-0 items-center gap-2 text-left"
          onClick={() => !isRunning && setExpanded((e) => !e)}
          disabled={isRunning}
          aria-expanded={expanded}
        >
          {isRunning && <WorkingOrb />}
          <span className="font-medium text-[var(--color-text-secondary)]">
            {label}
          </span>
          {toolCall.summary && !isRunning && (
            <span className="text-[var(--color-text-tertiary)] truncate ml-1">
              — {toolCall.summary}
            </span>
          )}
          {isError && (
            <span className="text-[var(--color-error)]">— Failed</span>
          )}
        </button>
        <span className="ml-auto flex items-center gap-2 shrink-0">
          {toolCall.artifact && onViewArtifact && !isRunning && (
            <button
              type="button"
              onClick={onViewArtifact}
              className="text-[var(--color-accent-primary)] hover:underline flex items-center gap-0.5"
            >
              View <ArrowUpRight className="h-3 w-3" />
            </button>
          )}
          {!isRunning &&
            (expanded ? (
              <ChevronDown className="h-3 w-3 text-[var(--color-text-tertiary)]" />
            ) : (
              <ChevronRight className="h-3 w-3 text-[var(--color-text-tertiary)]" />
            ))}
        </span>
      </div>

      {expanded && (
        <div className="px-3 pb-2.5 space-y-2.5">
          <div>
            <span className={MICRO_LABEL}>Input</span>
            <pre className="mt-1 text-[10px] font-mono text-[var(--color-text-secondary)] overflow-x-auto max-h-32 overflow-y-auto">
              {JSON.stringify(toolCall.arguments, null, 2)}
            </pre>
          </div>
          {toolCall.result && (
            <div>
              <span className={MICRO_LABEL}>Result</span>
              <pre className="mt-1 text-[10px] font-mono text-[var(--color-text-secondary)] overflow-x-auto max-h-48 overflow-y-auto">
                {JSON.stringify(toolCall.result, null, 2).slice(0, 2000)}
                {JSON.stringify(toolCall.result).length > 2000 ? "\n..." : ""}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
