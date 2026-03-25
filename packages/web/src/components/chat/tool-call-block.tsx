"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2, CheckCircle2, ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import type { ChatToolCall } from "@/lib/hooks/use-chat";

const TOOL_LABELS: Record<string, string> = {
  get_overview: "Getting codebase overview",
  get_context: "Looking up context",
  get_risk: "Assessing risk",
  get_why: "Querying decisions",
  search_codebase: "Searching codebase",
  get_dependency_path: "Tracing dependency path",
  get_dead_code: "Checking dead code",
  get_architecture_diagram: "Generating architecture diagram",
};

interface ToolCallBlockProps {
  toolCall: ChatToolCall;
  onViewArtifact?: () => void;
}

export function ToolCallBlock({ toolCall, onViewArtifact }: ToolCallBlockProps) {
  const [expanded, setExpanded] = useState(false);
  const label = TOOL_LABELS[toolCall.name] ?? toolCall.name;
  const isRunning = toolCall.status === "running";

  return (
    <div className="my-1.5 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] text-xs overflow-hidden">
      <button
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-[var(--color-bg-overlay)] transition-colors"
        onClick={() => !isRunning && setExpanded((e) => !e)}
      >
        {isRunning ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--color-accent-primary)] shrink-0" />
        ) : (
          <CheckCircle2 className="h-3.5 w-3.5 text-green-400 shrink-0" />
        )}
        <span className="font-medium text-[var(--color-text-secondary)]">
          {label}
        </span>
        {toolCall.summary && !isRunning && (
          <span className="text-[var(--color-text-tertiary)] truncate ml-1">
            — {toolCall.summary}
          </span>
        )}
        <span className="ml-auto flex items-center gap-1">
          {toolCall.artifact && onViewArtifact && !isRunning && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onViewArtifact();
              }}
              className="text-[var(--color-accent-primary)] hover:underline flex items-center gap-0.5"
            >
              View <ExternalLink className="h-3 w-3" />
            </button>
          )}
          {!isRunning && (
            expanded ? (
              <ChevronDown className="h-3 w-3 text-[var(--color-text-tertiary)]" />
            ) : (
              <ChevronRight className="h-3 w-3 text-[var(--color-text-tertiary)]" />
            )
          )}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-[var(--color-border-default)] px-3 py-2 space-y-2">
          <div>
            <span className="text-[10px] text-[var(--color-text-tertiary)] uppercase tracking-wider font-medium">
              Input
            </span>
            <pre className="mt-1 text-[10px] font-mono text-[var(--color-text-secondary)] overflow-x-auto max-h-32 overflow-y-auto">
              {JSON.stringify(toolCall.arguments, null, 2)}
            </pre>
          </div>
          {toolCall.result && (
            <div>
              <span className="text-[10px] text-[var(--color-text-tertiary)] uppercase tracking-wider font-medium">
                Result
              </span>
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
