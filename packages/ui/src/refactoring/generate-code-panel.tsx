"use client";

import * as React from "react";
import { Check, Copy, Loader2, RotateCw, Sparkles, TriangleAlert, Wand2 } from "lucide-react";
import { DiffView } from "./diff-view";
import {
  generatedVerdict,
  type GeneratedCode,
  type RefactoringPlan,
  type VerdictTone,
} from "./types";
import { toFriendlyMessage } from "../lib/errors";

/**
 * Run the opt-in LLM code generation for one plan: takes a deterministic plan,
 * returns the model's refactored code as a unified diff plus a self-check
 * verdict. The host supplies {@link GenerateCodePanelProps.onGenerate} (it owns
 * the API call); this component owns the async state, the diff rendering, and
 * the copy affordance. No auto-apply — the diff is the wedge.
 *
 * All of the logic lives here in the shared package so the hosted frontend
 * reuses it by passing a different `onGenerate`.
 */
export interface GenerateCodePanelProps {
  plan: RefactoringPlan;
  /** POST the generate-code endpoint and resolve the result. Host-owned. */
  onGenerate: (plan: RefactoringPlan) => Promise<GeneratedCode>;
}

type State =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "done"; result: GeneratedCode }
  | { status: "error"; message: string };

const VERDICT_STYLE: Record<VerdictTone, string> = {
  pass: "border-[var(--color-success)]/30 bg-[var(--color-success)]/10 text-[var(--color-success)]",
  fail: "border-[var(--color-caution)]/30 bg-[var(--color-caution)]/10 text-[var(--color-caution)]",
  neutral:
    "border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)]",
};

export function GenerateCodePanel({ plan, onGenerate }: GenerateCodePanelProps) {
  const [state, setState] = React.useState<State>({ status: "idle" });

  // A plan is identified by its id; reset when the host swaps the plan so the
  // panel never shows a stale diff for a different suggestion.
  React.useEffect(() => {
    setState({ status: "idle" });
  }, [plan.id]);

  const run = React.useCallback(async () => {
    setState({ status: "loading" });
    try {
      const result = await onGenerate(plan);
      setState({ status: "done", result });
    } catch (err) {
      const message =
        toFriendlyMessage(err, "Code generation failed. Check the provider config.");
      setState({ status: "error", message });
    }
  }, [onGenerate, plan]);

  if (state.status === "idle") {
    return (
      <button
        type="button"
        onClick={run}
        className="inline-flex items-center gap-2 rounded-lg border border-[var(--color-accent-primary)]/40 bg-[var(--color-accent-muted)] px-3.5 py-2 text-sm font-semibold text-[var(--color-accent-primary)] transition-colors hover:bg-[var(--color-accent-primary)] hover:text-[var(--color-bg-surface)]"
      >
        <Wand2 className="h-4 w-4" />
        Generate code
      </button>
    );
  }

  if (state.status === "loading") {
    return (
      <div className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
        <Loader2 className="h-4 w-4 animate-spin text-[var(--color-accent-primary)]" />
        Generating the refactored code…
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="space-y-2.5">
        <div className="flex items-start gap-2 rounded-lg border border-[var(--color-error)]/30 bg-[var(--color-error)]/5 px-3 py-2.5 text-sm text-[var(--color-text-secondary)]">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-error)]" />
          <span>{state.message}</span>
        </div>
        <button
          type="button"
          onClick={run}
          className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs font-semibold text-[var(--color-accent-primary)] transition-colors hover:bg-[var(--color-accent-muted)]"
        >
          <RotateCw className="h-3.5 w-3.5" />
          Try again
        </button>
      </div>
    );
  }

  return <GeneratedResult result={state.result} onRegenerate={run} />;
}

function GeneratedResult({
  result,
  onRegenerate,
}: {
  result: GeneratedCode;
  onRegenerate: () => void;
}) {
  const verdict = generatedVerdict(result);
  const tokens = result.input_tokens + result.output_tokens;

  return (
    <div className="space-y-3">
      {/* meta row — provider/model, cache, tokens, self-check verdict */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2.5 py-1 text-xs text-[var(--color-text-secondary)]">
          <Sparkles className="h-3.5 w-3.5 text-[var(--color-accent-primary)]" />
          {result.provider}
          {result.model ? <span className="text-[var(--color-text-tertiary)]">· {result.model}</span> : null}
        </span>
        {result.cached ? (
          <span className="rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2.5 py-1 text-xs text-[var(--color-text-tertiary)]">
            cached
          </span>
        ) : tokens > 0 ? (
          <span className="rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2.5 py-1 text-xs tabular-nums text-[var(--color-text-tertiary)]">
            {tokens.toLocaleString()} tokens
          </span>
        ) : null}
        {verdict ? (
          <span
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${VERDICT_STYLE[verdict.tone]}`}
            title={verdict.detail}
          >
            {verdict.tone === "pass" ? <Check className="h-3.5 w-3.5" /> : null}
            {verdict.label}
            {verdict.detail ? (
              <span className="font-normal opacity-80">· {verdict.detail}</span>
            ) : null}
          </span>
        ) : null}
        <div className="ml-auto flex items-center gap-1.5">
          <CopyButton text={result.diff || result.content} />
          <button
            type="button"
            onClick={onRegenerate}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border-default)] px-2 py-1 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-strong)] hover:text-[var(--color-text-primary)]"
            title="Generate again (bypasses the cache on the server only if the plan changed)"
          >
            <RotateCw className="h-3.5 w-3.5" />
            Regenerate
          </button>
        </div>
      </div>

      {/* the diff (or the raw response if the model emitted no diff fence) */}
      <DiffView
        diff={result.diff}
        emptyFallback={
          <pre className="max-h-[480px] overflow-auto rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3 text-[12px] leading-relaxed text-[var(--color-text-secondary)]">
            {result.content || "The model returned no code."}
          </pre>
        }
      />

      <p className="text-[11px] text-[var(--color-text-tertiary)]">
        Suggestion only — review the diff and apply it yourself or hand it to your coding agent.
      </p>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = React.useState(false);

  const copy = React.useCallback(() => {
    if (!text) return;
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    });
  }, [text]);

  return (
    <button
      type="button"
      onClick={copy}
      disabled={!text}
      className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border-default)] px-2 py-1 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-strong)] hover:text-[var(--color-text-primary)] disabled:opacity-50"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-[var(--color-success)]" /> : <Copy className="h-3.5 w-3.5" />}
      {copied ? "Copied" : "Copy diff"}
    </button>
  );
}
