"use client";

import {
  useEffect,
  useId,
  useRef,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
  type RefObject,
} from "react";
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
  /** Quiet conversation controls on the toolbar row (for example model choice). */
  footer?: ReactNode;
  /** Inside the box above the input: the context the message carries. */
  header?: ReactNode;
  /** One muted line under the box, such as a cost or accuracy note. */
  note?: ReactNode;
}

export const CHAT_COMPOSER_HINT = "Enter to send · Shift+Enter for newline";

// Past this height the input scrolls instead of growing.
const MAX_HEIGHT_PX = { page: 216, compact: 120 } as const;

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
  header,
  note,
}: ChatComposerProps) {
  const localRef = useRef<HTMLTextAreaElement>(null);
  const textareaRef = forwardedRef ?? localRef;
  const composingRef = useRef(false);
  const valueRef = useRef(value);
  valueRef.current = value;
  const reasonId = useId();
  const noteId = useId();
  const hasText = value.trim().length > 0;
  const showReason = disabled && Boolean(disabledReason);
  const contained = appearance === "contained";
  const canSend = hasText && !disabled && !isStreaming;

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    const max = compact || !contained ? MAX_HEIGHT_PX.compact : MAX_HEIGHT_PX.page;
    textarea.style.height = `${Math.min(textarea.scrollHeight, max)}px`;
  }, [compact, contained, textareaRef, value]);

  async function submit() {
    const text = value.trim();
    if (!text || isStreaming || disabled) return;
    onValueChange("");
    try {
      await onSend(text);
    } catch {
      // The host reports the failure; the question goes back in the box
      // unless something new was typed meanwhile.
      if (valueRef.current === "") onValueChange(text);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    // The Enter that confirms an IME candidate is not a send. Safari fires it
    // after compositionend with keyCode 229, so each check covers a browser.
    if (composingRef.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    void submit();
  }

  // A click on the box's padding or toolbar gap lands in the input.
  function focusInput(event: MouseEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement;
    // Controls, popovers opened from the toolbar, and the header (its text
    // stays selectable) keep their own mouse behaviour.
    if (
      target.closest(
        "button, a, input, select, textarea, [role='combobox'], [role='dialog'], [role='listbox'], [role='option'], [data-composer-header]",
      )
    ) {
      return;
    }
    event.preventDefault();
    textareaRef.current?.focus();
  }

  const sendButton = (
    <button
      type="button"
      className={cn(
        "relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-bg-surface)] disabled:cursor-not-allowed motion-reduce:transition-none",
        isStreaming || canSend
          ? "bg-[var(--color-action-primary)] text-[var(--color-text-on-action)] hover:bg-[var(--color-action-primary-hover)]"
          : "bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)]",
      )}
      onClick={isStreaming ? onCancel : () => void submit()}
      disabled={!isStreaming && !canSend}
      aria-label={isStreaming ? "Stop generation" : "Send message"}
      title={isStreaming ? "Stop generation" : "Send message"}
      {...(showReason ? { "aria-describedby": reasonId } : {})}
    >
      {isStreaming ? (
        <>
          {/* The turning arc says the answer is still coming; the square says what a click does. */}
          <span
            aria-hidden
            className="pointer-events-none absolute -inset-[3px] animate-spin rounded-full border-2 border-transparent border-t-[var(--color-text-tertiary)] [animation-duration:1.4s] motion-reduce:hidden"
          />
          <Square className="h-3 w-3 fill-current" />
        </>
      ) : (
        <ArrowUp className="h-4 w-4" strokeWidth={2.25} />
      )}
    </button>
  );

  const input = showReason ? (
    <p
      id={reasonId}
      className={cn(
        "min-w-0 flex-1 text-sm leading-6 text-[var(--color-text-secondary)]",
        contained ? (compact ? "px-3 pt-2.5" : "px-4 pt-3.5") : "py-1",
      )}
    >
      {disabledReason}
    </p>
  ) : (
    <textarea
      ref={textareaRef}
      value={value}
      onChange={(event) => onValueChange(event.target.value)}
      onKeyDown={handleKeyDown}
      onCompositionStart={() => {
        composingRef.current = true;
      }}
      onCompositionEnd={() => {
        composingRef.current = false;
      }}
      placeholder={placeholder}
      aria-label="Chat message"
      {...(note ? { "aria-describedby": noteId } : {})}
      disabled={disabled}
      autoFocus={autoFocus}
      enterKeyHint="send"
      rows={1}
      className={cn(
        // 16px: smaller text makes iOS zoom the page on focus.
        "block min-w-0 resize-none overflow-y-auto bg-transparent text-base leading-6 text-[var(--color-text-primary)] !outline-none placeholder:text-[var(--color-text-tertiary)] disabled:cursor-not-allowed disabled:opacity-60",
        !contained
          ? "max-h-[120px] flex-1 px-1 py-1"
          : compact
            ? "max-h-[120px] w-full px-3 pb-1 pt-2.5"
            : "max-h-[216px] min-h-[3.25rem] w-full px-4 pb-1 pt-3.5",
      )}
      style={{ scrollbarWidth: "thin" }}
    />
  );

  if (!contained) {
    return (
      <div
        data-chat-composer=""
        data-streaming={isStreaming || undefined}
        className={cn("min-w-0 border-t border-[var(--color-border-subtle)] pt-2", className)}
      >
        <div className="flex items-end gap-2">
          {input}
          {sendButton}
        </div>
      </div>
    );
  }

  return (
    <div className={cn("min-w-0", className)}>
      <div
        data-chat-composer=""
        data-streaming={isStreaming || undefined}
        onMouseDown={showReason ? undefined : focusInput}
        className={cn(
          "group/composer min-w-0 border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] shadow-[var(--shadow-sm)] transition-[border-color,box-shadow] motion-reduce:transition-none",
          compact ? "rounded-xl" : "rounded-[20px]",
          !showReason &&
            "cursor-text hover:border-[var(--color-border-hover)] focus-within:border-[var(--color-border-hover)] focus-within:shadow-[var(--shadow-sm),0_0_0_3px_color-mix(in_srgb,var(--color-focus-ring)_16%,transparent)]",
        )}
      >
        {header && (
          <div data-composer-header="" className={cn("min-w-0", compact ? "px-3 pt-2" : "px-4 pt-3")}>
            {header}
          </div>
        )}
        {input}
        <div
          className={cn(
            "flex min-h-10 items-center gap-2 text-xs text-[var(--color-text-tertiary)]",
            compact ? "px-2 pb-2" : "px-2.5 pb-2.5",
          )}
        >
          <div className="flex min-w-0 flex-1 items-center gap-1">{footer}</div>
          {!showReason && !compact && (
            // One hint, only while typing, and only where a keyboard and the
            // width for it are likely.
            <span className="hidden shrink-0 whitespace-nowrap opacity-0 transition-opacity group-focus-within/composer:opacity-100 motion-reduce:transition-none sm:pointer-fine:inline">
              {CHAT_COMPOSER_HINT}
            </span>
          )}
          {sendButton}
        </div>
      </div>
      {note && (
        <p id={noteId} className="mt-2 px-2 text-center text-[11px] leading-4 text-[var(--color-text-tertiary)]">
          {note}
        </p>
      )}
    </div>
  );
}
