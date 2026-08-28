import { cn } from "../lib/cn";
import { X } from "lucide-react";
import type { ChatContext } from "./chat-context";

export interface ChatContextIndicatorProps {
  context: ChatContext;
  className?: string;
  onRemove?: () => void;
}

export function ChatContextIndicator({
  context,
  className,
  onRemove,
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
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label="Remove current view from this message"
          title="Remove current view"
          className="ml-auto inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}
