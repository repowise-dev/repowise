"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Check,
  Copy,
  Sparkles,
  Bot,
  Code2,
  Wand2,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import type { AiPromptFlavor } from "./ai-prompt-builder";

export interface AiPromptModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Pure builder that returns the prompt string for the chosen flavor. */
  getPrompt: ((flavor: AiPromptFlavor) => string) | null;
  /** Path or other one-line identifier shown next to the title. */
  filePath?: string | null;
  /** Section heading (e.g. "AI fix prompt", "AI test prompt"). */
  title?: string;
  /** One-line subtitle below the title. */
  description?: string;
}

const FLAVORS: {
  value: AiPromptFlavor;
  label: string;
  Icon: React.ComponentType<{ className?: string }>;
  hint: string;
}[] = [
  { value: "generic", label: "Generic", Icon: Wand2, hint: "Any agent — Copilot, Codex, ChatGPT, custom." },
  { value: "claude-code", label: "Claude Code", Icon: Bot, hint: "Tuned for Claude Code's tools (Read / Edit / TodoWrite)." },
  { value: "claude-code-mcp", label: "Claude + repowise MCP", Icon: Sparkles, hint: "Steers the agent to repowise's MCP tools (get_context / get_risk / get_why) instead of re-grepping." },
  { value: "cursor", label: "Cursor", Icon: Code2, hint: "Uses @file context, Cursor editing conventions." },
];

const FLAVOR_STORAGE_KEY = "repowise:ai-prompt-flavor";

function loadStoredFlavor(): AiPromptFlavor {
  if (typeof window === "undefined") return "generic";
  try {
    const stored = window.localStorage.getItem(FLAVOR_STORAGE_KEY);
    if (stored && FLAVORS.some((f) => f.value === stored)) {
      return stored as AiPromptFlavor;
    }
  } catch {
    /* storage blocked */
  }
  return "generic";
}

export function AiPromptModal({
  open,
  onOpenChange,
  getPrompt,
  filePath,
  title = "AI fix prompt",
  description = "A ready-to-paste prompt that gives your AI coding agent every detail needed to make this change in one focused pass.",
}: AiPromptModalProps) {
  const [flavor, setFlavorState] = useState<AiPromptFlavor>(loadStoredFlavor);
  const [copied, setCopied] = useState(false);

  const setFlavor = (next: AiPromptFlavor) => {
    setFlavorState(next);
    try {
      window.localStorage.setItem(FLAVOR_STORAGE_KEY, next);
    } catch {
      /* storage blocked */
    }
  };

  const prompt = useMemo(
    () => (getPrompt ? getPrompt(flavor) : ""),
    [getPrompt, flavor],
  );

  useEffect(() => {
    if (!open) setCopied(false);
  }, [open]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard blocked — user can still select + Cmd/Ctrl-C */
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-[var(--color-accent-primary)]" />
            {title}
            {filePath ? (
              <span className="ml-2 text-xs font-mono font-normal text-[var(--color-text-tertiary)] truncate max-w-[260px]">
                {filePath}
              </span>
            ) : null}
          </DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <p className="text-xs uppercase tracking-wider text-[var(--color-text-tertiary)] mb-1.5">
              Target agent
            </p>
            <div className="grid grid-cols-2 gap-2">
              {FLAVORS.map((f) => {
                const active = flavor === f.value;
                return (
                  <button
                    key={f.value}
                    type="button"
                    onClick={() => setFlavor(f.value)}
                    className={
                      "rounded-md border px-3 py-2 text-left transition-colors " +
                      (active
                        ? "border-[var(--color-accent-primary)] bg-[var(--color-accent-muted)]"
                        : "border-[var(--color-border-default)] hover:border-[var(--color-border-strong)] hover:bg-[var(--color-bg-elevated)]")
                    }
                    title={f.hint}
                  >
                    <div className="flex items-center gap-1.5">
                      <f.Icon className={`h-3.5 w-3.5 ${active ? "text-[var(--color-accent-primary)]" : "text-[var(--color-text-tertiary)]"}`} />
                      <span className="text-xs font-semibold text-[var(--color-text-primary)]">
                        {f.label}
                      </span>
                    </div>
                    <p className="mt-0.5 text-xs text-[var(--color-text-tertiary)] leading-snug">
                      {f.hint}
                    </p>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-muted)] max-h-[420px] overflow-y-auto">
            <pre className="px-3 py-2 text-[11.5px] font-mono text-[var(--color-text-primary)] whitespace-pre-wrap break-words">
              {prompt}
            </pre>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--color-text-tertiary)]">
            <span>
              {prompt.length.toLocaleString()} chars · approx{" "}
              {Math.round(prompt.length / 4).toLocaleString()} tokens
            </span>
            <button
              type="button"
              onClick={handleCopy}
              disabled={!prompt}
              className={
                "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold transition-colors " +
                (copied
                  ? "bg-[var(--color-success)] text-[var(--color-text-inverse)]"
                  : "bg-[var(--color-accent-primary)] text-[var(--color-bg-surface)] hover:opacity-90")
              }
            >
              {copied ? (
                <>
                  <Check className="h-3.5 w-3.5" /> Copied
                </>
              ) : (
                <>
                  <Copy className="h-3.5 w-3.5" /> Copy prompt
                </>
              )}
            </button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
