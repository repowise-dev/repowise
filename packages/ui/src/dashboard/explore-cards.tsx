"use client";

import * as React from "react";
import { ArrowRight, MessageCircleQuestion, Waypoints } from "lucide-react";
import { Card } from "../ui/card";

interface KnowledgeGraphCardProps {
  /** Link to the repo's knowledge-graph page. */
  href: string;
}

/**
 * Overview tile that routes into the curated knowledge-graph view. The graph
 * is the richest surface in the product but lives only in the sidebar nav;
 * this card gives it a front-door on the landing page.
 */
export function KnowledgeGraphCard({ href }: KnowledgeGraphCardProps) {
  return (
    <Card className="overflow-hidden">
      <a
        href={href}
        className="group block h-full p-4 transition-colors hover:bg-[var(--color-bg-elevated)]"
      >
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text-primary)]">
            <Waypoints className="h-4 w-4 text-[var(--color-accent-primary)]" />
            Knowledge Graph
          </span>
          <ArrowRight className="h-4 w-4 text-[var(--color-text-tertiary)] transition-transform group-hover:translate-x-0.5" />
        </div>
        <p className="mt-3 text-sm text-[var(--color-text-secondary)]">
          A living map of this codebase: layers, communities, entry points, and
          the paths between them, rebuilt on every index.
        </p>
        <p className="mt-3 text-xs font-medium text-[var(--color-accent-primary)]">
          Explore the map
        </p>
      </a>
    </Card>
  );
}

interface AskAnythingCardProps {
  /** Called with the typed question; the host app routes it to chat. */
  onAsk: (question: string) => void;
  /** Example placeholder question. */
  placeholder?: string;
}

/**
 * Overview tile with a single input that drops the user straight into chat
 * with their question pre-asked. Chat accepts a `?q=` deep link; the host
 * wires `onAsk` to that navigation.
 */
export function AskAnythingCard({
  onAsk,
  placeholder = "How does authentication work?",
}: AskAnythingCardProps) {
  const [question, setQuestion] = React.useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (q) onAsk(q);
  }

  return (
    <Card className="flex h-full flex-col p-4">
      <span className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text-primary)]">
        <MessageCircleQuestion className="h-4 w-4 text-[var(--color-accent-primary)]" />
        Ask this codebase
      </span>
      <p className="mt-3 text-sm text-[var(--color-text-secondary)]">
        Plain-language answers with citations, grounded in the index.
      </p>
      <form onSubmit={handleSubmit} className="mt-auto flex items-center gap-2 pt-3">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={placeholder}
          aria-label="Ask a question about this codebase"
          className="h-9 w-full min-w-0 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-accent-primary)]"
        />
        <button
          type="submit"
          disabled={!question.trim()}
          aria-label="Ask"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-[var(--color-accent-fill)] text-[var(--color-text-on-accent)] transition-opacity hover:opacity-90 disabled:opacity-40"
        >
          <ArrowRight className="h-4 w-4" />
        </button>
      </form>
    </Card>
  );
}
