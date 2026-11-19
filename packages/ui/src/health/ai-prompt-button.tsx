"use client";

import { useEffect, useRef, useState, type MouseEvent, type ReactElement } from "react";
import { Check, ClipboardCopy } from "lucide-react";
import { cn } from "../lib/cn";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../ui/tooltip";

export interface AiPromptButtonProps {
  /** Runs on click; with `copyText` it runs as the copy starts, whatever its outcome. */
  onClick?: () => void;
  /**
   * The prompt itself, built on demand. When given, a click copies it, the
   * button says "Copied" for a moment, and hover or focus previews its
   * first lines. Without it the button only calls `onClick`, which usually
   * opens the prompt modal.
   */
  copyText?: () => string;
  /** Visible label; also the accessible name unless `ariaLabel` is given. */
  label?: string;
  /** The label shown for a moment after a copy. */
  copiedLabel?: string;
  /** The label shown for a moment when the clipboard refuses the copy. */
  failedLabel?: string;
  /**
   * `default` is a neutral outlined button for a detail view or a header.
   * `ghost` is icon + label with no border, for a per-row action (see
   * `RowPromptAction`). `icon` is icon-only for dense table rows; the label
   * rides along as the tooltip and accessible name.
   */
  variant?: "default" | "icon" | "ghost";
  /** Accessible name when the visible label repeats down a list, e.g. naming the row. */
  ariaLabel?: string;
  className?: string;
}

const COPIED_MS = 1600;
const PEEK_LINES = 4;

const BASE =
  "inline-flex items-center gap-1.5 rounded-md text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]";

const VARIANT: Record<NonNullable<AiPromptButtonProps["variant"]>, string> = {
  // Soft ink: a tonal fill so the most clicked action reads as one, quieter than the ink button.
  default:
    "h-7 border border-[var(--color-border-default)] bg-[var(--color-action-soft)] px-2.5 text-[var(--color-text-primary)] hover:border-[var(--color-border-hover)] hover:bg-[var(--color-action-soft-hover)]",
  ghost:
    "h-7 px-2 text-[var(--color-text-primary)] hover:bg-[var(--color-action-soft-hover)]",
  icon: "h-6 w-6 justify-center p-0 text-[var(--color-text-primary)] hover:bg-[var(--color-action-soft-hover)]",
};

/** The first few non-empty lines of a prompt, for the hover preview. */
export function promptPeek(prompt: string, lines = PEEK_LINES): string {
  const kept = prompt.split("\n").filter((line) => line.trim() !== "");
  const head = kept.slice(0, lines).join("\n");
  return kept.length > lines ? `${head}\n...` : head;
}

async function writeClipboard(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    // Clipboard blocked (insecure origin, denied permission).
    return false;
  }
}

/** Mounted only while the tooltip is open, so the prompt is built on demand. */
function PromptPeek({ build }: { build: () => string }) {
  return (
    <span className="block max-w-xs whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
      {promptPeek(build())}
    </span>
  );
}

type CopyResult = "copied" | "failed" | null;

/** The last copy's outcome, for a moment; cleared on unmount. */
function useCopyResult(): [CopyResult, (result: Exclude<CopyResult, null>) => void] {
  const [result, setResult] = useState<CopyResult>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  const flag = (next: Exclude<CopyResult, null>) => {
    setResult(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setResult(null), COPIED_MS);
  };
  return [result, flag];
}

/**
 * Canonical "hand this to your agent" affordance, used everywhere a prompt
 * can be generated so the action reads the same on every screen. Quiet on
 * purpose: neutral ink on a soft fill, with a copy icon. The host owns the
 * click (typically opening the shared AiPromptModal, so the label defaults to
 * "Agent prompt"), or passes `copyText` to copy straight away ("Copy prompt").
 */
export function AiPromptButton({
  onClick,
  copyText,
  label = copyText ? "Copy prompt" : "Agent prompt",
  copiedLabel = "Copied",
  failedLabel = "Couldn't copy",
  variant = "default",
  ariaLabel,
  className,
}: AiPromptButtonProps) {
  const [result, flagResult] = useCopyResult();
  const copied = result === "copied";
  const shown = { copied: copiedLabel, failed: failedLabel }[result ?? "copied"];

  function handleClick(e: MouseEvent<HTMLButtonElement>) {
    e.stopPropagation();
    if (copyText) void writeClipboard(copyText()).then((ok) => flagResult(ok ? "copied" : "failed"));
    onClick?.();
  }

  const Icon = copied ? Check : ClipboardCopy;
  const button = (
    <button
      type="button"
      onClick={handleClick}
      // A native title only where nothing else names the button on hover.
      title={variant === "icon" && !copyText ? label : undefined}
      aria-label={variant === "icon" ? (ariaLabel ?? label) : ariaLabel}
      className={cn(BASE, VARIANT[variant], className)}
    >
      <Icon
        aria-hidden
        className={cn("h-3.5 w-3.5 shrink-0", copied && "text-[var(--color-success)]")}
      />
      {variant !== "icon" && (result ? shown : label)}
    </button>
  );
  if (!copyText) return button;
  return (
    <WithPromptPeek build={copyText} announce={result ? shown : ""}>
      {button}
    </WithPromptPeek>
  );
}

/** The copy button with its prompt preview on hover or focus, and the polite announcement. */
function WithPromptPeek({
  build,
  announce,
  children,
}: {
  build: () => string;
  announce: string;
  children: ReactElement;
}) {
  return (
    <>
      <TooltipProvider delayDuration={300}>
        <Tooltip>
          <TooltipTrigger asChild>{children}</TooltipTrigger>
          <TooltipContent side="bottom" align="end">
            <PromptPeek build={build} />
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <span aria-live="polite" className="sr-only">
        {announce}
      </span>
    </>
  );
}
