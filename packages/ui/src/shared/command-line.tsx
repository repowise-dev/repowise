"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";

/** A shell command with a copy button; when the clipboard is blocked it stays selectable. */
export function CommandLine({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard blocked: the command stays selectable */
    }
  };
  return (
    <div className="mt-2 flex min-w-0 items-start gap-2 rounded bg-[var(--color-bg-inset)] px-2.5 py-1.5">
      <code className="min-w-0 flex-1 font-mono text-xs text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
        {command}
      </code>
      <button
        type="button"
        onClick={() => void copy()}
        aria-label={copied ? "Copied" : "Copy command"}
        className="inline-flex shrink-0 items-center gap-1 rounded text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        <span aria-hidden>{copied ? "Copied" : "Copy"}</span>
      </button>
    </div>
  );
}
