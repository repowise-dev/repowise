"use client";

import { useEffect, useRef, type ReactNode, type RefObject } from "react";
import { ArrowUp, Square } from "lucide-react";
import { cn } from "../lib/cn";

export interface ChatComposerProps {
  value: string;
  onValueChange: (value: string) => void;
  onSend: (text: string) => void | Promise<void>;
  onCancel: () => void;
  isStreaming: boolean;
  placeholder: string;
  disabled?: boolean;
  /** Why sending is unavailable (no provider, rate limit). Shown inside the
   *  composer in place of the input. */
  disabledReason?: ReactNode;
  autoFocus?: boolean;
  compact?: boolean;
  appearance?: "contained" | "bare";
  textareaRef?: RefObject<HTMLTextAreaElement | null>;
  className?: string;
  /** Quiet conversation controls rendered below the input (for example model choice). */
  footer?: ReactNode;
}

export const CHAT_COMPOSER_HINT = "Enter to send · Shift+Enter for newline";

/** Shared, controlled composer used by both the page chat and floating dock. */
export function ChatComposer({
  value,
  onValueChange,
  onSend,
  onCancel,
  isStreaming,
  placeholder,
  disabled = false,
  disabledReason,
  autoFocus = false,
  compact = false,
  appearance = "contained",
  textareaRef: forwardedRef,
  className,
  footer,
}: ChatComposerProps) {
  const localRef = useRef<HTMLTextAreaElement>(null);
  const textareaRef = forwardedRef ?? localRef;
  const hasText = value.trim().length > 0;
  const showReason = disabled && Boolean(disabledReason);
  const contained = appearance === "contained";

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
      data-chat-composer=""
      className={cn(
        "group/composer min-w-0",
        contained
          ? "rounded-2xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2 transition-colors focus-within:border-[var(--color-border-hover)] motion-reduce:transition-none"
          : "border-t border-[var(--color-border-subtle)] px-0 pb-0 pt-2",
        className,
      )}
    >
      <div className="flex items-end gap-2">
        {showReason ? (
          <p className="min-w-0 flex-1 py-1 text-sm leading-6 text-[var(--color-text-secondary)]">
            {disabledReason}
          </p>
        ) : (
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(event) => onValueChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
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
              // 16px: smaller text makes iOS zoom the page on focus.
              "min-w-0 flex-1 resize-none overflow-y-auto bg-transparent px-1 py-1 text-base leading-6 text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)] disabled:opacity-60",
              compact ? "max-h-24" : "max-h-36",
            )}
            style={{ scrollbarWidth: "none" }}
          />
        )}
        <button
          type="button"
          className={cn(
            "flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-bg-surface)] disabled:cursor-not-allowed motion-reduce:transition-none",
            isStreaming
              ? "border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] text-[var(--color-text-primary)] hover:border-[var(--color-border-hover)]"
              : hasText && !disabled
                ? "bg-[var(--color-accent-fill)] text-[var(--color-text-on-accent)] hover:bg-[var(--color-accent-fill-hover)]"
                : "bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)]",
          )}
          onClick={isStreaming ? onCancel : () => void submit()}
          disabled={(!hasText && !isStreaming) || disabled}
          aria-label={isStreaming ? "Stop generation" : "Send message"}
          title={isStreaming ? "Stop generation" : "Send message"}
        >
          {isStreaming ? <Square className="h-3 w-3 fill-current" /> : <ArrowUp className="h-4 w-4" />}
        </button>
      </div>
      {contained && (footer || !showReason) && (
        <div className="mt-1 flex min-h-6 items-center justify-between gap-2 px-0.5 text-xs text-[var(--color-text-tertiary)]">
          <div className="min-w-0">{footer}</div>
          {!showReason && (
            // One hint, only while typing, and only where a keyboard is likely.
            <span className="ml-auto hidden opacity-0 transition-opacity group-focus-within/composer:opacity-100 motion-reduce:transition-none pointer-fine:inline">
              {CHAT_COMPOSER_HINT}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
