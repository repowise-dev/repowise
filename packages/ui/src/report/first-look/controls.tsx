"use client";

import { useState, type ReactNode } from "react";
import {
  Bot,
  Braces,
  ChevronDown,
  Copy,
  Download,
  Eye,
  Link2,
  Printer,
  Share,
} from "lucide-react";
import type { FirstLookMark } from "@repowise-dev/types/first-look";
import { writeClipboard } from "../../health/ai-prompt-button";
import { cn } from "../../lib/cn";
import { Popover, PopoverContent, PopoverTrigger } from "../../ui/popover";
import type { FirstLookCopy } from "./copy";

/** Every control on the page carries this, so print and the export view drop them all. */
export const CONTROL = "first-look-control print:hidden";

export const GHOST =
  "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12.5px] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-inset)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

export const BUTTON =
  "inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-1.5 text-[13px] text-[var(--color-text-primary)] hover:border-[var(--color-border-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

const MENU_ROW =
  "flex w-full items-start gap-2.5 rounded-md px-2.5 py-2 text-left text-[13px] text-[var(--color-text-primary)] hover:bg-[var(--color-bg-inset)] focus-visible:bg-[var(--color-bg-inset)] focus-visible:outline-none";

/**
 * Copies text that is still being fetched. Safari only keeps the user's click
 * alive for a clipboard write it starts at once, so the promise goes into a
 * ClipboardItem where the browser has one; elsewhere it awaits, then writes.
 */
export async function copyLater(text: Promise<string>): Promise<boolean> {
  try {
    if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
      const blob = text.then((t) => new Blob([t], { type: "text/plain" }));
      await navigator.clipboard.write([new ClipboardItem({ "text/plain": blob })]);
      return true;
    }
  } catch {
    /* fall through to the plain write */
  }
  try {
    return await writeClipboard(await text);
  } catch {
    return false;
  }
}

function MenuRow({
  icon,
  label,
  hint,
  onSelect,
}: {
  icon?: ReactNode;
  label: string;
  hint?: string;
  onSelect: () => void;
}) {
  return (
    <button type="button" className={MENU_ROW} onClick={onSelect}>
      {icon ? <span className="mt-0.5 text-[var(--color-text-secondary)] [&_svg]:size-4">{icon}</span> : null}
      <span>
        {label}
        {hint ? <small className="block text-xs text-[var(--color-text-secondary)]">{hint}</small> : null}
      </span>
    </button>
  );
}

export type ShareAction = "link" | "agent" | "markdown" | "pdf" | "json" | "preview";

/** The top bar's Share menu. Each entry is the host's: the page only says which was picked. */
export function ShareMenu({
  copy,
  onAction,
  hasMarkdown,
}: {
  copy: FirstLookCopy;
  onAction: (action: ShareAction) => void;
  hasMarkdown: boolean;
}) {
  const [open, setOpen] = useState(false);
  const pick = (action: ShareAction) => () => {
    setOpen(false);
    onAction(action);
  };
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            BUTTON,
            "border-[var(--color-action-primary)] bg-[var(--color-action-primary)] text-[var(--color-text-on-action)] hover:border-[var(--color-action-primary-hover)] hover:bg-[var(--color-action-primary-hover)]",
          )}
        >
          <Share aria-hidden className="size-4" />
          {copy.share}
          <ChevronDown aria-hidden className="size-4" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="grid w-[260px] gap-0.5 rounded-xl bg-[var(--color-bg-surface)] p-1.5">
        <MenuRow icon={<Link2 />} label={copy.copyLink} hint={copy.copyLinkHint} onSelect={pick("link")} />
        <MenuRow icon={<Bot />} label={copy.copyForAgent} hint={copy.copyForAgentHint} onSelect={pick("agent")} />
        <hr className="mx-0.5 my-1 border-[var(--color-border-default)]" />
        {hasMarkdown ? (
          <MenuRow
            icon={<Download />}
            label={copy.downloadMarkdown}
            hint={copy.downloadMarkdownHint}
            onSelect={pick("markdown")}
          />
        ) : null}
        <MenuRow icon={<Printer />} label={copy.savePdf} hint={copy.savePdfHint} onSelect={pick("pdf")} />
        <MenuRow icon={<Braces />} label={copy.downloadJson} hint={copy.downloadJsonHint} onSelect={pick("json")} />
        <hr className="mx-0.5 my-1 border-[var(--color-border-default)]" />
        <MenuRow icon={<Eye />} label={copy.previewExport} hint={copy.previewExportHint} onSelect={pick("preview")} />
      </PopoverContent>
    </Popover>
  );
}

/** "Fix with agent" in a section header: one prompt for the section's open items. */
export function SectionPromptButton({
  copy,
  label,
  onCopy,
}: {
  copy: FirstLookCopy;
  /** The section, for the accessible name. */
  label: string;
  onCopy: () => void;
}) {
  return (
    <button
      type="button"
      className={cn(GHOST, CONTROL)}
      title={copy.fixWithAgentHint}
      aria-label={`${copy.fixWithAgent}: ${label}`}
      onClick={onCopy}
    >
      <Bot aria-hidden className="size-4" />
      {copy.fixWithAgent}
    </button>
  );
}

export function ItemPromptButton({
  copy,
  label,
  onCopy,
}: {
  copy: FirstLookCopy;
  /** The item, for the accessible name. */
  label: string;
  onCopy: () => void;
}) {
  return (
    <button
      type="button"
      className={cn(GHOST, CONTROL)}
      aria-label={`${copy.copyItemPrompt}: ${label}`}
      title={copy.copyItemPrompt}
      onClick={onCopy}
    >
      <Copy aria-hidden className="size-4" />
    </button>
  );
}

const NEEDS_REASON: ReadonlySet<FirstLookMark> = new Set(["ack", "false_positive"]);

/** Done, Acknowledge, Not an issue (both with a reason) or Snooze, for one item. */
export function MarkMenu({
  copy,
  label,
  onMark,
}: {
  copy: FirstLookCopy;
  /** The item, for the trigger's accessible name. */
  label: string;
  onMark: (mark: FirstLookMark, reason: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [asking, setAsking] = useState<FirstLookMark | null>(null);
  const [reason, setReason] = useState("");
  const close = () => {
    setOpen(false);
    setAsking(null);
    setReason("");
  };
  const choose = (mark: FirstLookMark) => () => {
    if (NEEDS_REASON.has(mark)) {
      setAsking(mark);
      return;
    }
    close();
    onMark(mark, null);
  };
  return (
    <Popover open={open} onOpenChange={(next) => (next ? setOpen(true) : close())}>
      <PopoverTrigger asChild>
        <button type="button" className={cn(GHOST, CONTROL)} aria-label={`${copy.mark}: ${label}`}>
          {copy.mark}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="grid w-[230px] rounded-xl bg-[var(--color-bg-surface)] p-1.5">
        {asking ? (
          <form
            className="grid gap-2 p-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              if (!reason.trim()) return;
              const mark = asking;
              const text = reason.trim();
              close();
              onMark(mark, text);
            }}
          >
            <label className="grid gap-1 text-xs text-[var(--color-text-secondary)]">
              {copy.reasonLabel}
              <input
                autoFocus
                required
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                className="rounded-md border border-[var(--color-field-border)] bg-[var(--color-field-bg)] px-2 py-1 text-[13px] text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
              />
            </label>
            <div className="flex justify-end gap-1.5">
              <button type="button" className={GHOST} onClick={() => setAsking(null)}>
                {copy.cancel}
              </button>
              <button type="submit" className={BUTTON}>
                {copy.save}
              </button>
            </div>
          </form>
        ) : (
          <div className="grid">
            <MenuRow label={copy.markDone} hint={copy.markDoneHint} onSelect={choose("done")} />
            <MenuRow label={copy.markAck} hint={copy.markAckHint} onSelect={choose("ack")} />
            <MenuRow
              label={copy.markFalsePositive}
              hint={copy.markFalsePositiveHint}
              onSelect={choose("false_positive")}
            />
            <MenuRow label={copy.markSnooze} onSelect={choose("snooze")} />
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
