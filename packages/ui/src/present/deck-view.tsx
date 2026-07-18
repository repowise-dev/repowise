"use client";

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ChevronLeft, ChevronRight, ExternalLink } from "lucide-react";
import { WikiMarkdown } from "../wiki/wiki-markdown";
import { MermaidDiagram } from "../wiki/mermaid-diagram";
import { cn } from "../lib/cn";
import { SlideProgress } from "./slide-progress";
import type { PresentSlide } from "./types";

export interface PresentLinkComponent {
  (props: { href: string; className?: string; children: React.ReactNode }): React.ReactElement;
}

interface DeckViewProps {
  slides: PresentSlide[];
  index: number;
  onIndex: (i: number) => void;
  onOpenPage?: ((pageId: string) => void) | undefined;
}

const FRESHNESS_DOT: Record<string, string> = {
  fresh: "var(--color-confidence-fresh)",
  stale: "var(--color-confidence-stale)",
  outdated: "var(--color-confidence-outdated)",
};

export function DeckView({ slides, index, onIndex, onOpenPage }: DeckViewProps) {
  const reduce = useReducedMotion();
  const slide = slides[index];
  if (!slide) return null;

  const atStart = index === 0;
  const atEnd = index === slides.length - 1;
  const isTitle = slide.kind === "title";
  const isDiagram = slide.kind === "diagram";
  // A layer slide pairing overview prose (left) with its diagram (right).
  const isSplit = slide.kind === "split";
  const isWide = isDiagram || isSplit;

  return (
    <div className="relative flex h-full flex-col">
      {/* Slide canvas */}
      <div
        className={cn(
          "relative flex flex-1 items-center justify-center overflow-hidden py-4",
          isWide ? "px-4 sm:px-8" : "px-6 sm:px-16",
        )}
      >
        <AnimatePresence mode="wait">
          <motion.div
            key={slide.id}
            initial={reduce ? false : { opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, y: -12 }}
            transition={{ duration: 0.22, ease: "easeOut" }}
            className={cn(
              "w-full",
              isTitle ? "max-w-3xl text-center" : isWide ? "max-w-6xl" : "max-w-3xl text-left",
            )}
          >
            {(() => {
              const eyebrowEl = slide.eyebrow ? (
                <p className="mb-3 text-xs font-semibold uppercase tracking-[0.14em] text-[var(--color-text-tertiary)]">
                  {slide.eyebrow}
                </p>
              ) : null;
              const titleEl = (
                <h2
                  className={cn(
                    "font-serif font-semibold tracking-tight text-[var(--color-text-primary)]",
                    isTitle
                      ? "text-5xl sm:text-6xl"
                      : isWide
                        ? "text-2xl sm:text-3xl"
                        : "text-3xl sm:text-4xl",
                  )}
                >
                  {slide.title}
                </h2>
              );
              const diagramEl = slide.mermaid ? (
                <div className={cn("flex justify-center", isSplit ? "" : "mt-4")}>
                  <div className="w-full">
                    <MermaidDiagram chart={slide.mermaid} securityLevel="loose" />
                  </div>
                </div>
              ) : null;
              const bodyEl = slide.bodyMarkdown ? (
                <div
                  className={cn(
                    "text-[var(--color-text-secondary)]",
                    isSplit ? "mt-4" : "mt-6",
                    isTitle ? "mx-auto max-w-xl text-lg" : "text-base",
                  )}
                >
                  <WikiMarkdown content={slide.bodyMarkdown} />
                </div>
              ) : null;
              const sourceEl =
                slide.sourcePageId && onOpenPage ? (
                  <button
                    type="button"
                    onClick={() => onOpenPage(slide.sourcePageId!)}
                    className={cn(
                      "mt-6 inline-flex items-center gap-1.5 text-xs font-medium text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-accent-primary)]",
                      isTitle && "mx-auto",
                    )}
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                    Open{slide.sourcePath ? ` ${slide.sourcePath}` : ""} in reader
                  </button>
                ) : null;

              // Split: prose left, diagram right; stacks on narrow screens.
              if (isSplit) {
                return (
                  <div className="grid items-center gap-8 md:grid-cols-2">
                    <div className="min-w-0 text-left">
                      {eyebrowEl}
                      {titleEl}
                      {bodyEl}
                      {sourceEl}
                    </div>
                    <div className="min-w-0">{diagramEl}</div>
                  </div>
                );
              }
              return (
                <>
                  {eyebrowEl}
                  {titleEl}
                  {diagramEl}
                  {bodyEl}
                  {sourceEl}
                </>
              );
            })()}
          </motion.div>
        </AnimatePresence>

        {/* Prev / next click zones */}
        <button
          type="button"
          onClick={() => onIndex(index - 1)}
          disabled={atStart}
          aria-label="Previous slide"
          className="absolute left-3 top-1/2 -translate-y-1/2 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]/70 p-2 text-[var(--color-text-secondary)] backdrop-blur transition-opacity hover:text-[var(--color-text-primary)] disabled:pointer-events-none disabled:opacity-0"
        >
          <ChevronLeft className="h-5 w-5" />
        </button>
        <button
          type="button"
          onClick={() => onIndex(index + 1)}
          disabled={atEnd}
          aria-label="Next slide"
          className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]/70 p-2 text-[var(--color-text-secondary)] backdrop-blur transition-opacity hover:text-[var(--color-text-primary)] disabled:pointer-events-none disabled:opacity-0"
        >
          <ChevronRight className="h-5 w-5" />
        </button>
      </div>

      {/* Footer: freshness + progress */}
      <div className="flex shrink-0 items-center justify-between px-6 pb-6 pt-2 sm:px-16">
        <div className="flex items-center gap-2">
          {slide.freshness && FRESHNESS_DOT[slide.freshness] && (
            <span
              className="h-1.5 w-1.5 rounded-full"
              style={{ background: FRESHNESS_DOT[slide.freshness] }}
              title={`Source is ${slide.freshness}`}
            />
          )}
        </div>
        <SlideProgress index={index} total={slides.length} onSelect={onIndex} />
        <span className="hidden text-[11px] text-[var(--color-text-tertiary)] sm:block">
          ← → to navigate
        </span>
      </div>
    </div>
  );
}
