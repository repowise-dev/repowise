import { cn } from "../lib/cn";
import type { ChatContext } from "./chat-context";

export interface ChatContextIndicatorProps {
  context: ChatContext;
  className?: string;
}

export function ChatContextIndicator({
  context,
  className,
}: ChatContextIndicatorProps) {
  return (
    <div
      className={cn(
        "flex min-w-0 items-baseline gap-2 px-1 pb-2 text-xs",
        className,
      )}
      aria-label={`Current view: ${context.label}${context.target ? `, ${context.target}` : ""}`}
    >
      <span className="shrink-0 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        Current view
      </span>
      <span className="shrink-0 text-[var(--color-text-secondary)]">
        {context.label}
      </span>
      {context.target && (
        <span className="min-w-0 font-mono text-[10px] text-[var(--color-text-tertiary)] break-all">
          {context.target}
        </span>
      )}
    </div>
  );
}
