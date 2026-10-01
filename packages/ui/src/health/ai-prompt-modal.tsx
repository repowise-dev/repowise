"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Copy, MessageCircleQuestion, Sparkles } from "lucide-react";
import type { ChatContext } from "@repowise-dev/types/chat";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { Skeleton, SkeletonRegion } from "../ui/skeleton";
import { useChatHandoff } from "../chat/chat-handoff";
import { ViewToggle } from "./code-health-controls";
import type { AiPromptFlavor } from "./ai-prompt-builder";

export interface AiPromptModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Pure builder that returns the prompt string for the chosen flavor. */
  getPrompt: ((flavor: AiPromptFlavor) => string) | null;
  /** Fetches the prompt instead, e.g. one core renders; overrides `getPrompt`.
   *  Pass a stable function: a new one fetches again. */
  promptSource?: PromptSource | undefined;
  /** Path or other one-line identifier shown next to the title. */
  filePath?: string | null;
  /** Section heading (e.g. "AI fix prompt", "AI test prompt"). */
  title?: string;
  /** One-line subtitle below the title. */
  description?: string;
  /** The one thing this prompt is about. With it the prompt can also be asked
   *  in chat; without it (a table-wide prompt) it can only be copied. */
  chatContext?: ChatContext | undefined;
}

/** The chat context for a prompt about one file, or none without a path. */
export function fileChatContext(path: string | null | undefined): ChatContext | undefined {
  return path ? { kind: "file", label: path, target: path, targetKind: "path" } : undefined;
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

/** Where a server-rendered prompt comes from, one request per flavor. */
export type PromptSource = (flavor: AiPromptFlavor) => Promise<string>;

type PromptLoad =
  | { status: "loading" }
  | { status: "ready"; text: string }
  | { status: "failed"; retry: () => void };

/** The prompt `source` returns for `flavor`, refetched on a flavor switch or a retry. */
function usePromptSource(source: PromptSource | undefined, flavor: AiPromptFlavor): PromptLoad | null {
  const [load, setLoad] = useState<PromptLoad>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!source) return;
    let live = true;
    setLoad({ status: "loading" });
    source(flavor).then(
      (text) => {
        if (live) setLoad({ status: "ready", text });
      },
      () => {
        if (live) setLoad({ status: "failed", retry: () => setAttempt((n) => n + 1) });
      },
    );
    return () => {
      live = false;
    };
  }, [source, flavor, attempt]);

  return source ? load : null;
}

/** The text, or while a fetched prompt is pending or failed, a stand-in for it. */
function PromptText({ text, load }: { text: string; load: PromptLoad | null }) {
  if (load?.status === "loading") {
    return (
      <SkeletonRegion className="space-y-2" label="Loading the prompt">
        <Skeleton className="h-3 w-3/4" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-5/6" />
        <Skeleton className="h-3 w-2/3" />
      </SkeletonRegion>
    );
  }
  if (load?.status === "failed") {
    return (
      <p className="text-xs text-[var(--color-text-secondary)]">
        Couldn't load the prompt.{" "}
        <button
          type="button"
          onClick={load.retry}
          className="rounded font-medium text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
        >
          Retry
        </button>
      </p>
    );
  }
  return (
    <pre className="min-w-0 max-w-full whitespace-pre-wrap [overflow-wrap:anywhere] font-mono text-xs leading-relaxed text-[var(--color-text-primary)]">
      {text}
    </pre>
  );
}

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

/**
 * Hands the prompt on screen to chat, seeded in the composer rather than sent.
 * Neutral beside the primary Copy pill, and absent when there is no single
 * subject or the host has chat controls off.
 */
function AskPromptInChat({
  prompt,
  context,
  onAsked,
}: {
  prompt: string;
  context: ChatContext | undefined;
  onAsked: (() => void) | undefined;
}) {
  const { request, askEnabled } = useChatHandoff();
  if (!context || !askEnabled) return null;
  return (
    <button
      type="button"
      onClick={() => {
        request({ context, question: prompt, autoSend: false });
        onAsked?.();
      }}
      disabled={!prompt}
      className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-primary)] transition-colors hover:border-[var(--color-border-hover)] hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] disabled:opacity-50 motion-reduce:transition-none"
    >
      <MessageCircleQuestion className="h-3.5 w-3.5" aria-hidden /> Ask in chat
    </button>
  );
}

/**
 * The prompt itself: which agent it is written for, the text, and a copy
 * button. Shared by the modal and by drawers that show the prompt inline, so
 * the chosen agent persists across both.
 */
export function AiPromptBlock({
  getPrompt,
  promptSource,
  bleed = "px-6",
  chatContext,
  onAsked,
}: {
  getPrompt: ((flavor: AiPromptFlavor) => string) | null;
  /** See {@link AiPromptModalProps.promptSource}. */
  promptSource?: PromptSource | undefined;
  /** Horizontal padding that matches the host's own, so the hairlines run edge to edge. */
  bleed?: string;
  /** What "Ask in chat" asks about; the control is hidden without it. */
  chatContext?: ChatContext | undefined;
  /** Called after the prompt is handed to chat, e.g. to close the host. */
  onAsked?: () => void;
}) {
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

  const load = usePromptSource(promptSource, flavor);
  const built = useMemo(
    () => (getPrompt && !promptSource ? getPrompt(flavor) : ""),
    [getPrompt, promptSource, flavor],
  );
  // Empty until a fetched prompt arrives, which keeps Copy and Ask disabled.
  const prompt = load ? (load.status === "ready" ? load.text : "") : built;

  useEffect(() => setCopied(false), [prompt]);

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
    <div className="min-w-0 space-y-4">
      {/* Full-bleed hairlines rather than a bordered, filled well. The
          prompt is the thing you opened this to read, not an object you
          can select or act on, so it does not earn a container. */}
      <div className="min-w-0 divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
        <div className={`min-w-0 space-y-2 py-4 ${bleed}`}>
          <p className="font-mono text-[10px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
            Target agent
          </p>
          <div className="max-w-full overflow-x-auto">
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

        <div className={`min-w-0 max-w-full max-h-[420px] overflow-x-hidden overflow-y-auto py-4 ${bleed}`}>
          <PromptText text={prompt} load={load} />
        </div>
      </div>

      <div className={`flex min-w-0 flex-wrap items-center justify-between gap-2 text-xs text-[var(--color-text-tertiary)] ${bleed}`}>
        <span className="min-w-0 tabular-nums">
          {load && !prompt ? null : (
            <>
              {prompt.length.toLocaleString()} chars, approx{" "}
              {Math.round(prompt.length / 4).toLocaleString()} tokens
            </>
          )}
        </span>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <AskPromptInChat prompt={prompt} context={chatContext} onAsked={onAsked} />
          <button
            type="button"
            onClick={handleCopy}
            disabled={!prompt}
            className={
              "inline-flex shrink-0 items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold transition-colors " +
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
    </div>
  );
}

export function AiPromptModal({
  open,
  onOpenChange,
  getPrompt,
  promptSource,
  filePath,
  title = "AI fix prompt",
  description = "A ready-to-paste prompt that gives your AI coding agent every detail needed to make this change in one focused pass.",
  chatContext,
}: AiPromptModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="min-w-0 w-[calc(100vw-2rem)] max-w-3xl overflow-hidden">
        <DialogHeader className="min-w-0">
          <DialogTitle className="flex min-w-0 items-center gap-2 pr-6">
            <Sparkles className="h-4 w-4 shrink-0 text-[var(--color-model)]" />
            <span className="min-w-0 truncate">{title}</span>
            {filePath ? (
              <span className="ml-2 min-w-0 max-w-[260px] flex-1 truncate font-mono text-xs font-normal text-[var(--color-text-tertiary)]">
                {filePath}
              </span>
            ) : null}
          </DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {/* The block's hairlines run to the modal's edge (`-mx-6` against its `p-6`). */}
        <div className="-mx-6 min-w-0">
          {open ? (
            <AiPromptBlock
              getPrompt={getPrompt}
              promptSource={promptSource}
              chatContext={chatContext}
              onAsked={() => onOpenChange(false)}
            />
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
