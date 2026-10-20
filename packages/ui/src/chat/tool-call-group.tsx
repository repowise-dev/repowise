"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "../lib/cn";
import { ToolCallBlock } from "./tool-call-block";
import { chatToolKind } from "./chat-stage";
import type { ChatArtifact, ChatUIToolCall } from "@repowise-dev/types/chat";

interface ToolCallGroupProps {
  toolCalls: ChatUIToolCall[];
  onViewArtifact?: (artifact: ChatArtifact) => void;
}

function plural(count: number, one: string, many: string) {
  return `${count} ${count === 1 ? one : many}`;
}

/** Pages a reading step covered: one per named target, else one. */
function pagesRead(toolCall: ChatUIToolCall) {
  const targets = toolCall.arguments.targets;
  return Array.isArray(targets) && targets.length > 0 ? targets.length : 1;
}

/** "Read 4 pages · 2 searches", from what the steps did. */
export function summarizeToolCalls(toolCalls: readonly ChatUIToolCall[]): string {
  let pages = 0;
  let searches = 0;
  let checks = 0;
  let failed = 0;
  for (const call of toolCalls) {
    if (call.status === "error") failed += 1;
    const kind = chatToolKind(call.name);
    if (call.origin === "grounding" || kind === "tracing") pages += pagesRead(call);
    else if (kind === "searching") searches += 1;
    else checks += 1;
  }
  return [
    pages > 0 ? `Read ${plural(pages, "page", "pages")}` : null,
    searches > 0 ? plural(searches, "search", "searches") : null,
    checks > 0 ? plural(checks, "check", "checks") : null,
    failed > 0 ? `${failed} failed` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * The model's work as one quiet line. A lone step reads as itself ("Read for
 * this page · src/a.py"); several collapse into a count ("Read 4 pages ·
 * 2 searches ›") that opens to the rows. No border, no ground.
 */
export function ToolCallGroup({ toolCalls, onViewArtifact }: ToolCallGroupProps) {
  const [expanded, setExpanded] = useState(false);

  if (toolCalls.length === 0) return null;

  const viewHandler = (call: ChatUIToolCall) => {
    const artifact = call.artifact;
    return artifact && onViewArtifact ? () => onViewArtifact(artifact) : undefined;
  };

  if (toolCalls.length === 1) {
    const call = toolCalls[0]!;
    const handler = viewHandler(call);
    return (
      <div data-activity-trail="true">
        <ToolCallBlock toolCall={call} {...(handler ? { onViewArtifact: handler } : {})} />
      </div>
    );
  }

  const summary = summarizeToolCalls(toolCalls);

  return (
    <div data-activity-trail="true" className="text-xs">
      <button
        type="button"
        className="inline-flex min-h-8 max-w-full items-center gap-1.5 rounded-md text-left text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
      >
        <span className="min-w-0 truncate tabular-nums">{summary}</span>
        <ChevronRight
          aria-hidden
          className={cn(
            "h-3 w-3 shrink-0 transition-transform motion-reduce:transition-none",
            expanded && "rotate-90",
          )}
        />
      </button>
      {expanded && (
        <div>
          {toolCalls.map((call) => {
            const handler = viewHandler(call);
            return (
              <ToolCallBlock
                key={call.id}
                toolCall={call}
                {...(handler ? { onViewArtifact: handler } : {})}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
