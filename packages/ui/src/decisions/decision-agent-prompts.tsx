"use client";

import * as React from "react";
import { Check, ClipboardCopy } from "lucide-react";
import { AiPromptButton, promptPeek } from "../health/ai-prompt-button";
import { Button } from "../ui/button";
import { Skeleton } from "../ui/skeleton";
import { cn } from "../lib/cn";

/** One decision prompt the host fetched, with the label its locale gives it. */
export interface DecisionAgentPrompt {
  /** The prompt as the server rendered it, the text `repowise decision discover` prints. */
  text: string;
  label: string;
}

export interface DecisionAgentPromptsProps {
  /**
   * The discovery pass: check the background records on the busiest files and
   * put the real ones in force. Pass it only when the server served `discover`,
   * so a store with nothing to check shows no button for it.
   */
  discover?: DecisionAgentPrompt | null;
  /** Move stored working agreements into CLAUDE.md. Omit when none is stored. */
  agreements?: DecisionAgentPrompt | null;
  /** Record the decisions nobody wrote down. */
  add?: DecisionAgentPrompt | null;
  /**
   * Prompts about one record (check it, pin or unpin it, veto it, change it),
   * in the order given. Each one the server rendered for that record's id.
   */
  prompts?: (DecisionAgentPrompt | null | undefined)[];
}

/**
 * The decisions page's agent actions, each a copy button over a prompt the
 * server rendered. The text is never built here: the same words reach the CLI,
 * so an agent run from the terminal and one handed the copied prompt do the
 * same pass. Renders nothing until a prompt is passed.
 */
export function DecisionAgentPrompts({
  discover,
  agreements,
  add,
  prompts = [],
}: DecisionAgentPromptsProps) {
  const shown = [discover, agreements, add, ...prompts].filter(
    (p): p is DecisionAgentPrompt => Boolean(p && p.text),
  );
  if (shown.length === 0) return null;
  return (
    <>
      {shown.map((p) => (
        <AiPromptButton key={p.label} copyText={() => p.text} label={p.label} />
      ))}
    </>
  );
}

/** The discovery prompt as the server returned it (`GET …/decisions/agent-prompt?kind=discover`). */
export interface DecisionAgentHeroPrompt {
  /**
   * `discover` while background records wait to be checked; the server turns
   * it into `add` on a store with nothing to check.
   */
  kind: string;
  text: string;
  /** How many records the prompt names. */
  count: number;
}

export interface DecisionAgentHeroProps {
  /**
   * The server's prompt, shown and copied exactly as returned. `undefined`
   * while it loads (a placeholder holds the space); `null` when it could not
   * be read, and the hero renders nothing.
   */
  prompt: DecisionAgentHeroPrompt | null | undefined;
  /** The small line over the heading. */
  eyebrow?: string;
  /** The heading; the default names the pass the prompt asks for. */
  heading?: string;
  /** One sentence on what the agent does with it. */
  description?: string;
  copyLabel?: string;
  copiedLabel?: string;
  failedLabel?: string;
  /** Small print under the button, e.g. which CLI command prints the same text. */
  footnote?: React.ReactNode;
  /** The page's other prompts and the manual path, as links or small buttons. */
  links?: React.ReactNode;
  /** A host's handoff beside the copy button, such as opening the prompt in an editor. */
  handoff?: React.ReactNode;
  /**
   * The pass over every background record, not only the ones the main prompt
   * names, as a secondary copy button beside it. Pass it only when the server
   * served the prompt (`?kind=review_all`) with text; otherwise nothing shows.
   */
  reviewAll?: DecisionAgentPrompt | null;
  /**
   * For a reader who cannot act on the repository: shown as one line in
   * place of the prompt and the button.
   */
  readOnlyNote?: string;
  /** Lines of the prompt shown in the preview well. */
  previewLines?: number;
  className?: string;
}

const HERO_MICRO =
  "font-mono text-caption uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

function defaultHeading(prompt: DecisionAgentHeroPrompt): string {
  if (prompt.kind !== "discover") return "Record the decisions this repository already follows";
  const n = prompt.count;
  return `Check ${n} background ${n === 1 ? "record" : "records"} on the busiest files`;
}

function defaultDescription(prompt: DecisionAgentHeroPrompt): string {
  return prompt.kind === "discover"
    ? "Your agent reads each record against the code and its commits, puts the real ones in force and dismisses the rest. What it puts in force shows up under Recent."
    : "Your agent reads the code and history and records the rules it clearly follows, each with the quote and commit it came from.";
}

/**
 * The decisions page's one handoff: the discovery prompt the server rendered,
 * previewed so a person sees what they hand over, behind one primary copy
 * button. The agent maintains the record; the person reads what it did.
 *
 * The text is never built here, for the same reason as {@link DecisionAgentPrompts}.
 */
export function DecisionAgentHero({
  prompt,
  eyebrow = "Hand off to your agent",
  heading,
  description,
  copyLabel = "Copy prompt for your agent",
  copiedLabel = "Copied. Paste it into your agent.",
  failedLabel = "Couldn't copy. Select the preview and copy it by hand.",
  footnote,
  links,
  handoff,
  reviewAll,
  readOnlyNote,
  previewLines = 6,
  className,
}: DecisionAgentHeroProps) {
  const headingId = React.useId();
  const [result, setResult] = React.useState<"copied" | "failed" | null>(null);
  const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  React.useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  if (readOnlyNote) {
    return <p className={cn("text-sm text-[var(--color-text-secondary)]", className)}>{readOnlyNote}</p>;
  }
  if (prompt === null || (prompt && !prompt.text)) return null;
  if (prompt === undefined) {
    return <Skeleton aria-hidden className={cn("h-[200px] w-full rounded-lg", className)} />;
  }

  const copy = async () => {
    let ok = true;
    try {
      await navigator.clipboard.writeText(prompt.text);
    } catch {
      ok = false;
    }
    setResult(ok ? "copied" : "failed");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setResult(null), 2400);
  };

  return (
    <section
      aria-labelledby={headingId}
      className={cn(
        "grid gap-3.5 rounded-lg border border-[var(--color-border-hover)] bg-[var(--color-bg-surface)] p-4 sm:px-5 sm:py-5",
        className,
      )}
    >
      <div className="grid min-w-0 gap-1.5">
        <p className={HERO_MICRO}>{eyebrow}</p>
        <h2
          id={headingId}
          className="text-base font-semibold text-[var(--color-text-primary)] [text-wrap:balance] sm:text-lg"
        >
          {heading ?? defaultHeading(prompt)}
        </h2>
        <p className="max-w-[64ch] text-sm text-[var(--color-text-secondary)]">
          {description ?? defaultDescription(prompt)}
        </p>
      </div>
      <div
        aria-label="Prompt preview"
        role="region"
        className="relative max-h-[124px] overflow-hidden rounded-md bg-[var(--color-bg-inset)] px-3.5 py-3"
      >
        <p className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
          {promptPeek(prompt.text, previewLines)}
        </p>
        <span
          aria-hidden
          className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-b from-transparent to-[var(--color-bg-inset)]"
        />
      </div>
      <div className="flex flex-wrap items-center gap-x-3.5 gap-y-2.5">
        <Button type="button" onClick={() => void copy()} className="w-full sm:w-auto">
          {result === "copied" ? <Check aria-hidden /> : <ClipboardCopy aria-hidden />}
          {copyLabel}
        </Button>
        <DecisionAgentPrompts prompts={[reviewAll]} />
        {handoff}
        <span role="status" aria-live="polite" className="text-xs text-[var(--color-text-secondary)]">
          {result === "copied" ? copiedLabel : result === "failed" ? failedLabel : ""}
        </span>
      </div>
      {(footnote || links) && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-[var(--color-border-default)] pt-3 text-xs text-[var(--color-text-tertiary)]">
          {footnote}
          {links}
        </div>
      )}
    </section>
  );
}
