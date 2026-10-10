"use client";

/**
 * One step of the model's work as a quiet text row. A finished step carries
 * no success badge: success is the default, so only a failure is marked. The
 * live progress marker belongs to the turn (see `chat-stage`), not the row.
 */

import { useMemo, useState } from "react";
import { ArrowUpRight, ChevronRight } from "lucide-react";
import { cn } from "../lib/cn";
import { HighlightedCodeBlock } from "../shared/code-block";
import type { ChatUIToolCall } from "@repowise-dev/types/chat";

const TOOL_LABELS: Record<string, string> = {
  get_overview: "Getting codebase overview",
  get_context: "Looking up context",
  get_symbol: "Reading symbol",
  get_risk: "Assessing risk",
  get_change_risk: "Scoring change risk",
  get_health: "Checking health",
  get_why: "Querying decisions",
  search_codebase: "Searching codebase",
  get_dead_code: "Checking dead code",
  get_answer: "Asking the index",
};

/** A step the server took for the page, before the model's first turn. */
const GROUNDING_LABEL = "Read for this page";

const RESULT_PREVIEW_CHARS = 2000;

/** `get_blast_radius` reads as "Blast radius" for a tool with no label. */
export function readableToolName(name: string): string {
  const words = name.replace(/^get_/, "").replace(/[_-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : name;
}

export function toolCallLabel(toolCall: ChatUIToolCall): string {
  if (toolCall.origin === "grounding") return GROUNDING_LABEL;
  return TOOL_LABELS[toolCall.name] ?? readableToolName(toolCall.name);
}

interface ToolCallBlockProps {
  toolCall: ChatUIToolCall;
  onViewArtifact?: () => void;
  /** @deprecated Rows no longer draw dividers. Ignored. */
  divided?: boolean;
}

export function ToolCallBlock({ toolCall, onViewArtifact }: ToolCallBlockProps) {
  const [expanded, setExpanded] = useState(false);
  const label = toolCallLabel(toolCall);
  const isRunning = toolCall.status === "running";
  const isError = toolCall.status === "error";

  // Serialised once per open, not on every render of a streaming turn.
  const input = useMemo(
    () => (expanded ? JSON.stringify(toolCall.arguments, null, 2) : ""),
    [expanded, toolCall.arguments],
  );
  const result = useMemo(() => {
    if (!expanded || !toolCall.result) return "";
    const full = JSON.stringify(toolCall.result, null, 2);
    return full.length > RESULT_PREVIEW_CHARS ? `${full.slice(0, RESULT_PREVIEW_CHARS)}\n...` : full;
  }, [expanded, toolCall.result]);

  return (
    <div data-tool-origin={toolCall.origin} className="text-xs">
      <div className="flex min-h-8 items-center gap-2">
        <button
          type="button"
          className="flex min-h-8 min-w-0 flex-1 items-center gap-1.5 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          onClick={() => !isRunning && setExpanded((e) => !e)}
          disabled={isRunning}
          aria-expanded={expanded}
        >
          <span className="shrink-0 text-[var(--color-text-secondary)]">{label}</span>
          {!isRunning && (toolCall.summary || isError) && (
            // The server already composes a failed summary as "Error: ...", so
            // a separate Failed badge beside it just says the same thing twice.
            <span
              title={toolCall.summary || "Failed"}
              className={cn(
                "min-w-0 truncate",
                isError ? "text-[var(--color-error)]" : "text-[var(--color-text-tertiary)]",
              )}
            >
              · {toolCall.summary || "Failed"}
            </span>
          )}
          {!isRunning && (
            <ChevronRight
              aria-hidden
              className={cn(
                "h-3 w-3 shrink-0 text-[var(--color-text-tertiary)] transition-transform motion-reduce:transition-none",
                expanded && "rotate-90",
              )}
            />
          )}
        </button>
        {toolCall.artifact && onViewArtifact && !isRunning && (
          <button
            type="button"
            onClick={onViewArtifact}
            className="inline-flex min-h-8 shrink-0 items-center gap-0.5 rounded-md px-1 text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          >
            View <ArrowUpRight aria-hidden className="h-3 w-3" />
          </button>
        )}
      </div>

      {expanded && (
        <div className="pb-1">
          <HighlightedCodeBlock code={input} language="json" label="Input" compact className="my-1.5" />
          {result && (
            <HighlightedCodeBlock code={result} language="json" label="Result" compact className="my-1.5" />
          )}
        </div>
      )}
    </div>
  );
}
