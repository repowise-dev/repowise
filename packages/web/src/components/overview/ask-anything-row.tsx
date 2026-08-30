"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Sparkles } from "lucide-react";

const PLACEHOLDER = "How does the incremental pipeline decide what to re-index?";

/** Openers that show what this is good at: tracing behaviour, locating a
 *  concern, and asking for rationale. Concrete beats "Ask me anything" — most
 *  people stall at a blank box because they cannot tell what it can answer. */
const SUGGESTIONS = [
  "How does authentication work?",
  "Where is the retry logic?",
  "Why is this module structured this way?",
];

/**
 * One input row that deep-links into chat.
 *
 * The card this replaces was one of four equal tiles at the bottom of the
 * page, which put a text input beside a chart and a blurb as though they were
 * the same kind of object. Asking a question is an action, so it gets a
 * control, not a card.
 *
 * The ember rail and the sparkle are the page's only ornament, spent here on
 * purpose: this is the one row that does something rather than reporting
 * something, and it needs to look different enough to be found.
 */
export function AskAnythingRow({ repoId }: { repoId: string }) {
  const router = useRouter();
  const [question, setQuestion] = useState("");

  function ask(q: string) {
    const trimmed = q.trim();
    if (!trimmed) return;
    router.push(`/repos/${repoId}/chat?q=${encodeURIComponent(trimmed)}`);
  }

  return (
    <div className="flex flex-col gap-2.5">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          ask(question);
        }}
        className="group relative flex items-center gap-2.5 overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] py-2 pl-3.5 pr-2 transition-colors focus-within:border-[var(--color-border-hover)]"
      >
        {/* Plum rail on the leading edge. This was the Code Health hero's
            ember, borrowed so the two read as related; the row is now on the
            model accent, and a warm rail under a plum icon and a plum submit
            would read as two systems arguing over one control. Same device,
            same quarter width, following the row's own meaning. */}
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-[3px]"
          style={{ background: "var(--gradient-plum)" }}
        />
        <Sparkles
          aria-hidden
          className="h-3.5 w-3.5 shrink-0 text-[var(--color-model)] transition-transform group-focus-within:scale-110"
        />
        <label htmlFor="overview-ask" className="sr-only">
          Ask a question about this codebase
        </label>
        <input
          id="overview-ask"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={PLACEHOLDER}
          className="min-w-0 flex-1 bg-transparent text-xs text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] focus:outline-none"
        />
        <button
          type="submit"
          disabled={!question.trim()}
          className="inline-flex shrink-0 items-center gap-1 rounded-md bg-[var(--color-model)] px-2.5 py-1 text-xs font-medium text-[var(--color-text-on-model)] transition hover:bg-[var(--color-model-hover)] disabled:opacity-40"
        >
          Ask
          <ArrowRight className="h-3 w-3" />
        </button>
      </form>

      <div className="flex flex-wrap gap-1.5">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => ask(s)}
            className="rounded-full border border-[var(--color-border-default)] px-2.5 py-1 text-[11px] text-[var(--color-text-tertiary)] transition-colors hover:border-[var(--color-model)] hover:text-[var(--color-model)]"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
