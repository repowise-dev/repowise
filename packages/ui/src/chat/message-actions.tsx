"use client";

import { useState } from "react";
import { Check, Copy, Pencil, RotateCcw, Send } from "lucide-react";
import type { ChatUIMessage } from "@repowise-dev/types/chat";

interface MessageActionsProps {
  message: ChatUIMessage;
  onRetry?: () => void | Promise<void>;
  onEditAndResend?: (text: string) => void | Promise<void>;
  onFollowUp?: (text: string) => void;
}

export function MessageActions({ message, onRetry, onEditAndResend, onFollowUp }: MessageActionsProps) {
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.text);
  if (message.isStreaming || !message.text) return null;

  if (editing) {
    return <form className="mt-2 flex gap-2" onSubmit={(event) => { event.preventDefault(); const text = draft.trim(); if (text) void onEditAndResend?.(text); setEditing(false); }}><textarea autoFocus value={draft} onChange={(event) => setDraft(event.target.value)} aria-label="Edit message" className="min-h-20 flex-1 resize-y rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-2 text-sm text-[var(--color-text-primary)] outline-none focus:border-[var(--color-accent-primary)]" /><button type="submit" className="flex h-10 w-10 items-center justify-center rounded-md bg-[var(--color-model)] text-[var(--color-text-on-model)]" aria-label="Fork and resend"><Send className="h-4 w-4" /></button></form>;
  }

  const actionClass = "inline-flex min-h-8 items-center gap-1 rounded-md px-1.5 text-[11px] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]";
  return <div className="mt-1.5 flex flex-wrap items-center gap-1" aria-label={`${message.role} message actions`}><button type="button" className={actionClass} onClick={() => { void navigator.clipboard.writeText(message.text); setCopied(true); window.setTimeout(() => setCopied(false), 1600); }}>{copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}{copied ? "Copied" : "Copy"}</button>{message.role === "assistant" && onRetry && <button type="button" className={actionClass} onClick={() => void onRetry()}><RotateCcw className="h-3 w-3" />Retry</button>}{message.role === "user" && message.serverId && onEditAndResend && <button type="button" className={actionClass} onClick={() => setEditing(true)}><Pencil className="h-3 w-3" />Edit and resend</button>}{message.role === "assistant" && onFollowUp && <button type="button" className={actionClass} onClick={() => onFollowUp("Can you go deeper on the most important part?")}>Follow up</button>}</div>;
}
