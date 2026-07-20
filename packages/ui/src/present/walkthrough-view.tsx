"use client";

import { Check, Clock, ChevronLeft, ChevronRight, ExternalLink } from "lucide-react";
import { WikiMarkdown } from "../wiki/wiki-markdown";
import { cn } from "../lib/cn";
import type { PresentStep } from "./types";

interface WalkthroughViewProps {
  steps: PresentStep[];
  index: number;
  onIndex: (i: number) => void;
  totalMinutes: number;
  onOpenPage?: ((pageId: string) => void) | undefined;
}

export function WalkthroughView({ steps, index, onIndex, totalMinutes, onOpenPage }: WalkthroughViewProps) {
  const step = steps[index];
  if (!step) return null;

  const pct = ((index + 1) / steps.length) * 100;

  return (
    <div className="flex h-full min-h-0">
      {/* Step rail */}
      <aside className="hidden w-72 shrink-0 flex-col border-r border-[var(--color-border-default)] bg-[var(--color-bg-surface)] md:flex">
        <div className="shrink-0 border-b border-[var(--color-border-default)] px-4 py-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)]">
              Walkthrough
            </span>
            <span className="inline-flex items-center gap-1 rounded-full bg-[var(--color-bg-elevated)] px-2 py-0.5 text-[11px] text-[var(--color-text-secondary)]">
              <Clock className="h-3 w-3" />~{totalMinutes} min
            </span>
          </div>
          <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-[var(--color-border-active)]">
            <div className="h-full rounded-full bg-[var(--color-accent-primary)] transition-all" style={{ width: `${pct}%` }} />
          </div>
        </div>
        <nav className="flex-1 overflow-y-auto p-2">
          {steps.map((s, i) => {
            const done = i < index;
            const current = i === index;
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => onIndex(i)}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                  current
                    ? "bg-[var(--color-accent-muted)] text-[var(--color-text-primary)]"
                    : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]",
                )}
              >
                <span
                  className={cn(
                    "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold",
                    done
                      ? "bg-[var(--color-accent-primary)] text-[var(--color-text-on-accent)]"
                      : current
                        ? "border border-[var(--color-accent-primary)] text-[var(--color-accent-primary)]"
                        : "border border-[var(--color-border-active)] text-[var(--color-text-tertiary)]",
                  )}
                >
                  {done ? <Check className="h-3 w-3" /> : i + 1}
                </span>
                <span className="min-w-0 flex-1 truncate">{s.title}</span>
              </button>
            );
          })}
        </nav>
      </aside>

      {/* Content pane */}
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto px-6 py-8 sm:px-12">
          <div className="mx-auto max-w-3xl">
            <p className="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-[var(--color-text-tertiary)]">
              Step {index + 1} of {steps.length} · ~{step.estMinutes} min
            </p>
            <h2 className="font-serif text-3xl font-semibold tracking-tight text-[var(--color-text-primary)]">
              {step.title}
            </h2>
            {step.reason && (
              <div className="mt-4 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-4 py-3">
                <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)]">
                  Why this matters
                </p>
                <p className="mt-1 text-sm text-[var(--color-text-secondary)]">{step.reason}</p>
              </div>
            )}
            {step.bodyMarkdown ? (
              <div className="mt-6 text-base text-[var(--color-text-secondary)]">
                <WikiMarkdown content={step.bodyMarkdown} />
              </div>
            ) : (
              <p className="mt-6 text-sm text-[var(--color-text-tertiary)]">
                No generated page for this file yet. Open it in the reader to read the source.
              </p>
            )}
            {step.sourcePageId && onOpenPage && (
              <button
                type="button"
                onClick={() => onOpenPage(step.sourcePageId!)}
                className="mt-6 inline-flex items-center gap-1.5 text-xs font-medium text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-accent-primary)]"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                Open{step.targetPath ? ` ${step.targetPath}` : ""} in reader
              </button>
            )}
          </div>
        </div>

        {/* Footer nav */}
        <div className="flex shrink-0 items-center justify-between border-t border-[var(--color-border-default)] px-6 py-3 sm:px-12">
          <button
            type="button"
            onClick={() => onIndex(index - 1)}
            disabled={index === 0}
            className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] disabled:pointer-events-none disabled:opacity-40"
          >
            <ChevronLeft className="h-4 w-4" />
            Prev
          </button>
          <button
            type="button"
            onClick={() => onIndex(index + 1)}
            disabled={index === steps.length - 1}
            className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-accent-fill)] px-4 py-1.5 text-sm font-medium text-[var(--color-text-on-accent)] shadow-[var(--shadow-button)] transition-[filter] hover:brightness-[1.06] disabled:pointer-events-none disabled:opacity-40"
          >
            Next stop
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
