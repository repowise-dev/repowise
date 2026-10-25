"use client";

import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { AiPromptButton } from "@repowise-dev/ui/health/ai-prompt-button";
import { RowPromptAction } from "@repowise-dev/ui/health/row-prompt-action";
import { buildSetupAiPrompt } from "@repowise-dev/ui/health/ai-prompt-builder";
import { cn } from "@/lib/utils/cn";
import type { SetupMsg, SetupRow, SetupTone } from "./setup-rows";

const DOT: Record<SetupTone, string> = {
  done: "bg-[var(--color-success)]",
  waiting: "bg-[var(--color-warning)]",
  attention: "bg-[var(--color-warning)]",
  off: "bg-[var(--color-text-tertiary)]",
  found: "bg-[var(--color-info)]",
  model: "bg-[var(--color-model)]",
};

/** The same quiet underlined toggle as "Show files". */
const TOGGLE =
  "text-xs font-medium text-[var(--color-text-primary)] underline decoration-[var(--color-border-hover)] underline-offset-[3px]";

/** A `SetupMsg` in the reader's locale; doctor text passes through as is. */
export function useSetupText() {
  const t = useTranslations("setup.row");
  return (m: SetupMsg) => ("text" in m ? m.text : t(m.key, m.values));
}

export function StatusDot({ tone, className }: { tone: SetupTone; className?: string }) {
  return (
    <span aria-hidden className={cn("inline-block h-[7px] w-[7px] shrink-0 rounded-full", DOT[tone], className)} />
  );
}

/** The terminal command a row stands for, in mono. */
export function SetupCommand({ command }: { command: string }) {
  return (
    <code className="max-w-full rounded bg-[var(--color-bg-inset)] px-1.5 py-px font-mono text-[11px] text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
      {command}
    </code>
  );
}

interface Props {
  row: SetupRow;
  /** The row's one primary action (a button or switch), if it has one. */
  action?: ReactNode;
  /** Opens the prompt modal; rows outside "Needs attention" use it. */
  onOpenPrompt: () => void;
  /** Extra content under the detail, e.g. the skipped files. */
  children?: ReactNode;
}

/**
 * One Setup row, the same for every section: a status dot and the title,
 * one line of detail (the rest behind "Show details"), then the terminal
 * command; actions sit on the right. The status word is for screen readers;
 * the dot carries it on screen. A "Needs attention" row's action copies its
 * prompt; elsewhere the prompt shows on row hover, focus, and on touch.
 */
export function SetupRowView({ row, action, onOpenPrompt, children }: Props) {
  const text = useSetupText();
  const attention = row.action?.kind === "prompt";
  const title = text(row.title);
  return (
    <div
      data-row={row.id}
      className="group/row flex flex-col gap-2 border-t border-[var(--color-border-default)] py-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4"
    >
      <div className="min-w-0 flex-1">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text-primary)]">
          <StatusDot tone={row.tone} />
          <span className="min-w-0">{title}</span>
          <span className="sr-only">: {text(row.status)}</span>
        </h3>
        <RowDetail detail={text(row.detail)} />
        {children}
        <div className="mt-1.5">
          <SetupCommand command={row.command} />
        </div>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {attention ? <AttentionPrompt row={row} /> : action}
        {!attention && <RowPromptAction onClick={onOpenPrompt} subject={title} />}
      </div>
    </div>
  );
}

/**
 * The detail on one line, with a toggle when it runs longer. Overflow is
 * measured, so a short line never grows a toggle it does not need.
 */
function RowDetail({ detail }: { detail: string }) {
  const t = useTranslations("setup");
  const ref = useRef<HTMLParagraphElement>(null);
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || open) return;
    const measure = () => setOverflows(el.scrollHeight > el.clientHeight + 1);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [detail, open]);

  if (!detail) return null;
  return (
    <>
      <p
        ref={ref}
        className={cn(
          "mt-0.5 text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]",
          !open && "line-clamp-1",
        )}
      >
        {detail}
      </p>
      {(overflows || open) && (
        <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)} className={cn(TOGGLE, "mt-1")}>
          {open ? t("hideDetails") : t("showDetails")}
        </button>
      )}
    </>
  );
}

/** Copies the row's prompt; the button itself says "Copied". */
function AttentionPrompt({ row }: { row: SetupRow }) {
  const t = useTranslations("setup");
  return (
    <AiPromptButton
      label={t("copyPrompt")}
      copiedLabel={t("copied")}
      failedLabel={t("copyFailed")}
      ariaLabel={`${t("copyPrompt")}: ${row.prompt.title}`}
      copyText={() => buildSetupAiPrompt(row.prompt)}
    />
  );
}
