"use client";

import { useEffect, useRef } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { BookOpen, ChevronLeft, ChevronRight } from "lucide-react";
import { WikiMarkdown } from "../wiki/wiki-markdown";
import { MermaidDiagram } from "../wiki/mermaid-diagram";
import { cn } from "../lib/cn";
import { SlideProgress } from "./slide-progress";
import type { PresentSlide, StartGroup } from "./types";

type OpenPage = ((pageId: string) => void) | undefined;

interface DeckViewProps {
  slides: PresentSlide[];
  index: number;
  onIndex: (i: number) => void;
  onOpenPage?: OpenPage;
}

const FOCUS =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]";

// Fresh is the quiet default; only a source that needs attention is marked.
const FRESHNESS: Record<string, { color: string; word: string }> = {
  stale: { color: "var(--color-confidence-stale)", word: "Source page is stale" },
  outdated: { color: "var(--color-confidence-outdated)", word: "Source page is outdated" },
};

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <p className="mb-3 font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--color-text-tertiary)]">
      {children}
    </p>
  );
}

function Body({ markdown, large }: { markdown: string; large?: boolean }) {
  return (
    <div
      className={cn(
        "mt-5 max-w-[65ch] [&_p]:mb-0 [&_p]:leading-[1.65]",
        large ? "[&_p]:text-[18px]" : "[&_p]:text-[16px]",
      )}
    >
      <WikiMarkdown content={markdown} />
    </div>
  );
}

function Steps({ steps }: { steps: string[] }) {
  return (
    <div className="mt-8">
      <Eyebrow>In this part</Eyebrow>
      <ol className="border-t border-[var(--color-border-default)]">
        {steps.map((step, i) => (
          <li
            key={step}
            className="flex gap-3 border-b border-[var(--color-border-default)] py-2.5 text-[15px] text-[var(--color-text-primary)]"
          >
            <span className="w-5 shrink-0 pt-px font-mono text-[12px] tabular-nums text-[var(--color-text-tertiary)]">
              {i + 1}
            </span>
            <span className="min-w-0">{step}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function StartList({ groups, onOpenPage }: { groups: StartGroup[]; onOpenPage: OpenPage }) {
  return (
    <ul className="mt-8 border-t border-[var(--color-border-default)]">
      {groups.map((group, i) => (
        <li key={i} className="border-b border-[var(--color-border-default)] py-4">
          {group.label && (
            <p className="mb-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--color-text-tertiary)]">
              {group.label}
            </p>
          )}
          <div className="flex flex-col items-start gap-1">
            {group.files.map((file) =>
              file.pageId && onOpenPage ? (
                <button
                  key={file.path}
                  type="button"
                  onClick={() => onOpenPage(file.pageId!)}
                  className={cn(
                    "max-w-full break-all rounded-sm text-left font-mono text-[15px] text-[var(--color-text-primary)] underline decoration-[var(--color-border-active)] underline-offset-4 transition-colors hover:text-[var(--color-accent-primary)] hover:decoration-[var(--color-accent-primary)]",
                    FOCUS,
                  )}
                >
                  {file.path}
                </button>
              ) : (
                <span key={file.path} className="max-w-full break-all font-mono text-[15px] text-[var(--color-text-secondary)]">
                  {file.path}
                </span>
              ),
            )}
          </div>
          {group.note && (
            <p className="mt-1.5 max-w-[65ch] text-[15px] text-[var(--color-text-secondary)]">{group.note}</p>
          )}
        </li>
      ))}
    </ul>
  );
}

function SourceLink({ slide, onOpenPage }: { slide: PresentSlide; onOpenPage: OpenPage }) {
  const fresh = slide.freshness ? FRESHNESS[slide.freshness] : undefined;
  if (!fresh && !(slide.sourcePageId && onOpenPage)) return null;
  return (
    <div className="mt-8 flex flex-wrap items-center gap-x-5 gap-y-2 text-[12px]">
      {slide.sourcePageId && onOpenPage && (
        <button
          type="button"
          onClick={() => onOpenPage(slide.sourcePageId!)}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-sm font-medium text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-accent-primary)]",
            FOCUS,
          )}
        >
          <BookOpen className="h-3.5 w-3.5" aria-hidden />
          Open in docs
        </button>
      )}
      {fresh && (
        <span className="inline-flex items-center gap-1.5 text-[var(--color-text-tertiary)]">
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: fresh.color }} aria-hidden />
          {fresh.word}
        </span>
      )}
    </div>
  );
}

function SlideContent({ slide, onOpenPage }: { slide: PresentSlide; onOpenPage: OpenPage }) {
  const diagram = slide.mermaid ? <MermaidDiagram chart={slide.mermaid} securityLevel="loose" /> : null;
  const heading = (
    <h2
      className={cn(
        "font-serif font-semibold tracking-tight text-[var(--color-text-primary)]",
        slide.kind === "title"
          ? "text-[32px] leading-[1.1] sm:text-[52px]"
          : "text-[24px] leading-tight sm:text-[32px]",
      )}
    >
      {slide.title}
    </h2>
  );
  const prose = (
    <>
      {slide.eyebrow && <Eyebrow>{slide.eyebrow}</Eyebrow>}
      {heading}
      {slide.body && <Body markdown={slide.body} large={slide.kind !== "start"} />}
    </>
  );

  // A part with a diagram pairs its prose with the picture; stacks when narrow.
  if (slide.kind === "part" && diagram) {
    return (
      <div className="grid items-start gap-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-12">
        <div className="min-w-0">
          {prose}
          {slide.steps && slide.steps.length > 0 && <Steps steps={slide.steps} />}
          <SourceLink slide={slide} onOpenPage={onOpenPage} />
        </div>
        <div className="min-w-0">{diagram}</div>
      </div>
    );
  }
  return (
    <>
      {prose}
      {diagram && <div className="mt-6">{diagram}</div>}
      {slide.steps && slide.steps.length > 0 && <Steps steps={slide.steps} />}
      {slide.start && slide.start.length > 0 && (
        <StartList groups={slide.start} onOpenPage={onOpenPage} />
      )}
      <SourceLink slide={slide} onOpenPage={onOpenPage} />
    </>
  );
}

export function DeckView({ slides, index, onIndex, onOpenPage }: DeckViewProps) {
  const reduce = useReducedMotion();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const slide = slides[index];
  // Each slide opens at its top, not where the previous one was scrolled to.
  useEffect(() => {
    scrollRef.current?.scrollTo?.({ top: 0 });
  }, [index]);
  if (!slide) return null;

  const wide = !!slide.mermaid;
  const navButton = cn(
    "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] disabled:pointer-events-none disabled:opacity-40",
    FOCUS,
  );

  return (
    <div className="flex h-full flex-col">
      {/* Focusable so Up/Down scroll a slide taller than the screen. */}
      <div
        ref={scrollRef}
        tabIndex={0}
        aria-label="Slide content"
        className={cn("min-h-0 flex-1 overflow-y-auto", FOCUS, "focus-visible:ring-inset")}
      >
        <div className="flex min-h-full items-center px-4 py-10 sm:px-16">
          <AnimatePresence mode="wait" initial={false}>
            <motion.section
              key={slide.id}
              aria-label={slide.title}
              initial={reduce ? false : { opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduce ? { opacity: 1 } : { opacity: 0 }}
              transition={{ duration: reduce ? 0 : 0.18, ease: "easeOut" }}
              className={cn("mx-auto w-full min-w-0", wide ? "max-w-6xl" : "max-w-3xl")}
            >
              <SlideContent slide={slide} onOpenPage={onOpenPage} />
            </motion.section>
          </AnimatePresence>
        </div>
      </div>

      <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-[var(--color-border-default)] px-4 py-3 sm:px-16">
        <button
          type="button"
          onClick={() => onIndex(index - 1)}
          disabled={index === 0}
          title="Previous (Left arrow)"
          className={navButton}
        >
          <ChevronLeft className="h-4 w-4" aria-hidden />
          <span className="hidden sm:inline">Previous</span>
          <span className="sr-only sm:hidden">Previous slide</span>
        </button>
        <SlideProgress index={index} total={slides.length} title={slide.title} onSelect={onIndex} />
        <button
          type="button"
          onClick={() => onIndex(index + 1)}
          disabled={index === slides.length - 1}
          title="Next (Right arrow or Space)"
          className={navButton}
        >
          <span className="hidden sm:inline">Next</span>
          <span className="sr-only sm:hidden">Next slide</span>
          <ChevronRight className="h-4 w-4" aria-hidden />
        </button>
      </footer>
    </div>
  );
}
