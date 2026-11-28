"use client";

import { useState } from "react";
import { Check, Copy, Pencil, RotateCcw } from "lucide-react";
import { cn } from "../lib/cn";
import type { ChatUIMessage } from "@repowise-dev/types/chat";

interface MessageActionsProps {
  message: ChatUIMessage;
  onRetry?: () => void | Promise<void>;
  onEditAndResend?: (text: string) => void | Promise<void>;
  /** Why this answer failed. Shown inline with Retry even when no text arrived. */
  error?: string | null;
  /** Keep the row visible without hover (the newest answer). */
  pinned?: boolean;
  align?: "start" | "end";
}

const ICON_BUTTON =
  "inline-flex h-8 w-8 items-center justify-center rounded-md text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

/**
 * Copy, retry and edit under a turn. Revealed on hover or focus with a fine
 * pointer; always shown on touch screens and on the newest answer, so nothing
 * is reachable only by hovering.
 */
export function MessageActions({
  message,
  onRetry,
  onEditAndResend,
  error,
  pinned = false,
  align = "start",
}: MessageActionsProps) {
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.text);
  if (message.isStreaming) return null;
  if (!message.text && !error) return null;

  if (editing) {
    return (
      <MessageEditForm
        draft={draft}
        setDraft={setDraft}
        onSubmit={(text) => onEditAndResend?.(text)}
        onCancel={() => {
          setDraft(message.text);
          setEditing(false);
        }}
        onDone={() => setEditing(false)}
      />
    );
  }

  const copy = () => {
    void navigator.clipboard.writeText(message.text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div className="mt-2 space-y-1.5 print:hidden">
      {error && <ErrorNotice error={error} onRetry={onRetry} />}
      {message.text && (
        <div
          role="group"
          aria-label={`${message.role} message actions`}
          className={cn(
            "flex items-center gap-0.5 transition-opacity motion-reduce:transition-none",
            align === "end" && "justify-end",
            !pinned &&
              "pointer-fine:opacity-0 pointer-fine:group-hover/turn:opacity-100 group-focus-within/turn:opacity-100",
          )}
        >
          <button
            type="button"
            className={ICON_BUTTON}
            onClick={copy}
            aria-label={copied ? "Copied" : "Copy"}
            title="Copy"
          >
            {copied ? <Check aria-hidden className="h-3.5 w-3.5" /> : <Copy aria-hidden className="h-3.5 w-3.5" />}
          </button>
          <FollowUpButtons
            message={message}
            error={error}
            onRetry={onRetry}
            onEditAndResend={onEditAndResend}
            onEdit={() => setEditing(true)}
          />
        </div>
      )}
    </div>
  );
}

function MessageEditForm({
  draft,
  setDraft,
  onSubmit,
  onCancel,
  onDone,
}: {
  draft: string;
  setDraft: (text: string) => void;
  onSubmit: (text: string) => void | Promise<void>;
  onCancel: () => void;
  onDone: () => void;
}) {
  return (
    <form
      className="mt-2 w-full space-y-2 print:hidden"
      onSubmit={(event) => {
        event.preventDefault();
        const text = draft.trim();
        if (text) void onSubmit(text);
        onDone();
      }}
    >
      <textarea
        autoFocus
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        aria-label="Edit message"
        className="min-h-20 w-full resize-y rounded-2xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-4 py-2.5 text-base leading-relaxed text-[var(--color-text-primary)] !outline-none focus:border-[var(--color-border-hover)] focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
      />
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="inline-flex h-8 items-center rounded-full px-3 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
        >
          Cancel
        </button>
        <button
          type="submit"
          aria-label="Fork and resend"
          className="inline-flex h-8 items-center rounded-full bg-[var(--color-action-primary)] px-3 text-xs font-medium text-[var(--color-text-on-action)] hover:bg-[var(--color-action-primary-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-bg-root)]"
        >
          Send
        </button>
      </div>
    </form>
  );
}

function ErrorNotice({
  error,
  onRetry,
}: {
  error: string;
  onRetry: (() => void | Promise<void>) | undefined;
}) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
      <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-error)]" />
      <span className="min-w-0 text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">{error}</span>
      {onRetry && (
        <button
          type="button"
          onClick={() => void onRetry()}
          className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
        >
          <RotateCcw aria-hidden className="h-3.5 w-3.5" />
          Retry
        </button>
      )}
    </div>
  );
}

/** Retry under an answer, edit-and-resend under a saved question. */
function FollowUpButtons({
  message,
  error,
  onRetry,
  onEditAndResend,
  onEdit,
}: {
  message: ChatUIMessage;
  error: string | null | undefined;
  onRetry: (() => void | Promise<void>) | undefined;
  onEditAndResend: ((text: string) => void | Promise<void>) | undefined;
  onEdit: () => void;
}) {
  return (
    <>
      {message.role === "assistant" && onRetry && !error && (
        <button type="button" className={ICON_BUTTON} onClick={() => void onRetry()} aria-label="Retry" title="Retry">
          <RotateCcw aria-hidden className="h-3.5 w-3.5" />
        </button>
      )}
      {message.role === "user" && message.serverId && onEditAndResend && (
        <button
          type="button"
          className={ICON_BUTTON}
          onClick={onEdit}
          aria-label="Edit and resend"
          title="Edit and resend"
        >
          <Pencil aria-hidden className="h-3.5 w-3.5" />
        </button>
      )}
    </>
  );
}
