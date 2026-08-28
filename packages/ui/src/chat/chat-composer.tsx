"use client";

import { useEffect, useRef, type RefObject } from "react";
import { Send, StopCircle } from "lucide-react";
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
        "flex items-end gap-2",
        appearance === "contained"
          ? "rounded-2xl border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-4 py-3 focus-within:border-[var(--color-accent-primary)]"
          : "border-t border-[var(--color-border-subtle)] px-0 pb-0 pt-3",
        disabled && "opacity-60",
        compact && appearance === "contained" && "rounded-xl px-3 py-2.5",
        className,
      )}
    >
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
          "min-w-0 flex-1 resize-none bg-transparent text-[15px] leading-6 text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)] overflow-y-auto",
          compact ? "max-h-24" : "max-h-36",
        )}
        style={{ scrollbarWidth: "none" }}
      />
      <Button
        size="icon"
        className="h-8 w-8 shrink-0 rounded-xl"
        onClick={isStreaming ? onCancel : () => void submit()}
        disabled={(!value.trim() && !isStreaming) || disabled}
        aria-label={isStreaming ? "Stop generation" : "Send message"}
        title={isStreaming ? "Stop generation" : "Send message"}
      >
        {isStreaming ? (
          <StopCircle className="h-4 w-4" />
        ) : (
          <Send className="h-4 w-4" />
        )}
      </Button>
    </div>
  );
}
