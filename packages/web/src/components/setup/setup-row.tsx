"use client";

import { useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { Check } from "lucide-react";
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
 * One Setup row: status dot and word, title, one line of detail, then the
 * action column: the primary action, "Copy prompt for your agent" and the
 * terminal command. The prompt shows on hover and focus-within, always on
 * touch screens, and on a "Needs attention" row it is the primary action,
 * with the prompt it copies previewed under the row.
 */
export function SetupRowView({ row, action, onOpenPrompt, children }: Props) {
  const text = useSetupText();
  const attention = row.action?.kind === "prompt";
  const title = text(row.title);
  const [copied, setCopied] = useState(false);
  return (
    <div
      data-row={row.id}
      className={cn(
        "group/row grid grid-cols-1 gap-x-4 gap-y-2 border-t border-[var(--color-border-default)] py-3 sm:grid-cols-[124px_minmax(0,1fr)_auto]",
        attention &&
          "-mx-2.5 rounded-lg border-transparent bg-[var(--color-warning-muted)] px-2.5 [&+&]:mt-1",
      )}
    >
      <span className="flex items-center gap-1.5 pt-px text-xs text-[var(--color-text-secondary)]">
        <StatusDot tone={row.tone} />
        {text(row.status)}
      </span>
      <div className="min-w-0">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{title}</h3>
        <p className="mt-0.5 text-xs text-[var(--color-text-secondary)] [text-wrap:pretty]">
          {text(row.detail)}
        </p>
        {children}
      </div>
      <div className="flex flex-wrap items-center gap-2 sm:flex-col sm:flex-nowrap sm:items-end">
        {attention ? <AttentionPrompt row={row} onCopied={() => setCopied(true)} /> : action}
        {!attention && <RowPromptAction onClick={onOpenPrompt} subject={title} />}
        <SetupCommand command={row.command} />
      </div>
      {attention && <PromptPreview row={row} copied={copied} />}
    </div>
  );
}

/** Copies the prompt on click; the preview under the row says it did. */
function AttentionPrompt({ row, onCopied }: { row: SetupRow; onCopied: () => void }) {
  const t = useTranslations("setup");
  return (
    <AiPromptButton
      label={t("copyPrompt")}
      ariaLabel={`${t("copyPrompt")}: ${row.prompt.title}`}
      onClick={() => {
        void copyText(buildSetupAiPrompt(row.prompt)).then((ok) => ok && onCopied());
      }}
    />
  );
}

async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    // Clipboard blocked: the preview stays selectable.
    return false;
  }
}

function PromptPreview({ row, copied }: { row: SetupRow; copied: boolean }) {
  const t = useTranslations("setup");
  return (
    <div className="col-span-full min-w-0 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-inset)] px-3 py-2.5">
      <p className="mb-1 flex items-center justify-between gap-2 text-xs font-medium text-[var(--color-text-primary)]">
        {t("promptPreview")}
        <span aria-live="polite" className="flex items-center gap-1 font-normal text-[var(--color-text-tertiary)]">
          {copied && (
            <>
              <Check className="h-3 w-3 text-[var(--color-success)]" aria-hidden />
              {t("copied")}
            </>
          )}
        </span>
      </p>
      <p className="line-clamp-4 font-mono text-[11px] leading-relaxed text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
        {previewText(row)}
      </p>
    </div>
  );
}

/** The part of the prompt worth reading at a glance: what was found, what to run, the ask. */
export function previewText(row: SetupRow): string {
  const { state, items, fix } = row.prompt;
  const listed = items?.length ? ` ${items.slice(0, 5).join(", ")}${items.length > 5 ? ", ..." : ""}.` : "";
  const ask = fix ? " Do not change anything until I confirm." : "";
  return `In ${row.prompt.repoName}: ${state}${listed} Run ${row.command} to confirm.${ask}`;
}
