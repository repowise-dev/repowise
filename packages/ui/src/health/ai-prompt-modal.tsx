"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Copy, Sparkles } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { ViewToggle } from "./code-health-controls";
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

/** The four target agents, in the order the segmented control renders them.
 *  No per-flavor icon: four icons on four segments decorate a choice that its
 *  own label already names, and the hint for the active one says the rest. */
const FLAVORS: { value: AiPromptFlavor; label: string; hint: string }[] = [
  { value: "generic", label: "Generic", hint: "Any agent: Copilot, Codex, ChatGPT, custom." },
  { value: "claude-code", label: "Claude Code", hint: "Tuned for Claude Code's tools (Read / Edit / TodoWrite)." },
  {
    value: "claude-code-mcp",
    label: "Claude + MCP",
    hint: "Steers the agent to repowise's MCP tools (get_context / get_risk / get_why) instead of re-grepping.",
  },
  { value: "cursor", label: "Cursor", hint: "Uses @file context and Cursor editing conventions." },
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
            <Sparkles className="h-4 w-4 text-[var(--color-model)]" />
            {title}
            {filePath ? (
              <span className="ml-2 text-xs font-mono font-normal text-[var(--color-text-tertiary)] truncate max-w-[260px]">
                {filePath}
              </span>
            ) : null}
          </DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Full-bleed hairlines rather than a bordered, filled well. The
              prompt is the thing you opened this to read, not an object you
              can select or act on, so it does not earn a container — and a
              second plane inside a floating panel is one plane too many. The
              rules run to the modal's edge (`-mx-6` against its `p-6`) so they
              read as the page's section dividers do, rather than as a box that
              happens to have lost its sides. */}
          <div className="-mx-6 divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
            <div className="space-y-2 px-6 py-4">
              <p className="font-mono text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                Target agent
              </p>
              <div className="w-fit">
                <ViewToggle
                  value={flavor}
                  options={FLAVORS.map((f) => ({ value: f.value, label: f.label }))}
                  onChange={setFlavor}
                />
              </div>
              <p className="text-xs leading-snug text-[var(--color-text-tertiary)]">
                {FLAVORS.find((f) => f.value === flavor)?.hint}
              </p>
            </div>

            <div className="max-h-[420px] overflow-y-auto px-6 py-4">
              <pre className="font-mono text-xs leading-relaxed text-[var(--color-text-primary)] whitespace-pre-wrap break-words">
                {prompt}
              </pre>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--color-text-tertiary)]">
            <span className="tabular-nums">
              {prompt.length.toLocaleString()} chars, approx{" "}
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
                  : "bg-[var(--color-model)] text-[var(--color-text-on-model)] hover:bg-[var(--color-model-hover)]")
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
