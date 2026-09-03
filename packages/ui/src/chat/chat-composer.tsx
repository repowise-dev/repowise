"use client";

import { useEffect, useRef, type ReactNode, type RefObject } from "react";
import { ArrowUp, Square } from "lucide-react";
import { Button } from "../ui/button";
import { cn } from "../lib/cn";

export interface ChatComposerProps {
  value: string;
  onValueChange: (value: string) => void;
  onSend: (text: string) => void | Promise<void>;
  onCancel: () => void;
  isStreaming: boolean;
  placeholder: string;
  disabled?: boolean;
  autoFocus?: boolean;
  compact?: boolean;
  appearance?: "contained" | "bare";
  textareaRef?: RefObject<HTMLTextAreaElement | null>;
  className?: string;
  /** Quiet conversation controls rendered below the input (for example model choice). */
  footer?: ReactNode;
}

/** Shared, controlled composer used by both the page chat and floating dock. */
export function ChatComposer({
  value,
  onValueChange,
  onSend,
  onCancel,
  isStreaming,
  placeholder,
  disabled = false,
  autoFocus = false,
  compact = false,
  appearance = "contained",
  textareaRef: forwardedRef,
  className,
  footer,
}: ChatComposerProps) {
  const localRef = useRef<HTMLTextAreaElement>(null);
  const textareaRef = forwardedRef ?? localRef;

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, compact ? 96 : 144)}px`;
  }, [compact, textareaRef, value]);

  async function submit() {
    const text = value.trim();
    if (!text || isStreaming || disabled) return;
    onValueChange("");
    await onSend(text);
  }

  return (
    <div
      className={cn(
        "min-w-0",
        appearance === "contained"
          ? "rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2.5 py-1.5 focus-within:border-[var(--color-accent-primary)]"
          : "border-t border-[var(--color-border-subtle)] px-0 pb-0 pt-2",
        disabled && "opacity-60",
        compact && appearance === "contained" && "px-2 py-1.5",
        className,
      )}
    >
      <div className="flex items-end gap-2">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(event) => onValueChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void submit();
            }
          }}
          placeholder={placeholder}
          aria-label="Chat message"
          disabled={disabled}
          autoFocus={autoFocus}
          rows={1}
          className={cn(
            "min-w-0 flex-1 resize-none bg-transparent px-1 py-0.5 text-[15px] leading-6 text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)] overflow-y-auto",
            compact ? "max-h-24" : "max-h-36",
          )}
          style={{ scrollbarWidth: "none" }}
        />
        <Button
          variant="ghost"
          size="icon"
          className={cn(
            "h-9 w-9 shrink-0 rounded-md border border-[var(--color-border-default)]",
            "bg-[var(--color-bg-elevated)] text-[var(--color-text-primary)]",
            "hover:border-[var(--color-border-hover)] hover:bg-[var(--color-bg-overlay)]",
          )}
          onClick={isStreaming ? onCancel : () => void submit()}
          disabled={(!value.trim() && !isStreaming) || disabled}
          aria-label={isStreaming ? "Stop generation" : "Send message"}
          title={isStreaming ? "Stop generation" : "Send message"}
        >
          {isStreaming ? <Square className="h-3.5 w-3.5 fill-current" /> : <ArrowUp className="h-4 w-4" />}
        </Button>
      </div>
      {footer && (
        <div className="mt-0.5 flex min-h-6 items-center justify-between gap-2 px-0.5 text-xs text-[var(--color-text-tertiary)]">
          <div className="min-w-0">{footer}</div>
          <span className="ml-auto hidden sm:inline">Shift+Enter for newline</span>
        </div>
      )}
    </div>
  );
}
