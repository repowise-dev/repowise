"use client";

import type { ReactNode } from "react";
import { Loader2, RefreshCw, Sparkles } from "lucide-react";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { cn } from "../lib/cn";

export type RegenerateMode = "write" | "regenerate";

export type RegenerateCascade = "none" | "dependents" | "full";

export interface RegenerateButtonProps {
  /** "write" for an unwritten (template) page — an inviting, accented
   *  "Write with AI"; "regenerate" for a model-written page — a quiet
   *  "Regenerate". Defaults to "regenerate". */
  mode?: RegenerateMode;
  /** Fired when the user clicks the button. Wrapper owns opening the confirm
   *  dialog, the mutation, the toast, and cache-invalidation. */
  onRegenerate: () => void;
  /** When true, the button shows a spinner and is disabled (request in-flight
   *  prior to a job id being assigned). */
  isLoading?: boolean;
  /** When true, the progress dialog is open. The wrapper sets this to `true`
   *  once it receives a job id back from the regenerate mutation. */
  isInProgress?: boolean;
  /** Called when the progress dialog requests to close (dismiss or job done). */
  onDialogClose?: () => void;
  /** Rendered inside the in-progress dialog — typically a job-progress widget
   *  owned by the wrapper. */
  jobSlot?: ReactNode;
  /** Compact, link-like trigger for placing the affordance inside page body
   *  copy (e.g. next to the provenance pill) rather than a toolbar. The
   *  progress dialog is unchanged. */
  inline?: boolean;
}

export function RegenerateButton({
  mode = "regenerate",
  onRegenerate,
  isLoading = false,
  isInProgress = false,
  onDialogClose,
  jobSlot,
  inline = false,
}: RegenerateButtonProps) {
  const isWrite = mode === "write";
  const Icon = isLoading ? Loader2 : isWrite ? Sparkles : RefreshCw;

  return (
    <>
      {inline ? (
        <button
          type="button"
          onClick={onRegenerate}
          disabled={isLoading || isInProgress}
          className="inline-flex items-center gap-1 rounded-full border border-[var(--color-accent-primary)]/40 bg-[var(--color-accent-muted)] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-accent-primary)] transition-colors hover:bg-[var(--color-accent-muted)] hover:text-[var(--color-accent-hover)] disabled:opacity-60"
          aria-label={isWrite ? "Write this page with AI" : "Regenerate this page"}
        >
          <Icon className={cn("h-2.5 w-2.5", isLoading && "animate-spin")} />
          {isWrite ? "Write with AI" : "Regenerate"}
        </button>
      ) : (
        <Button
          variant={isWrite ? "outline" : "ghost"}
          size="sm"
          onClick={onRegenerate}
          disabled={isLoading || isInProgress}
          className={cn(
            "h-7 gap-1.5 text-xs",
            isWrite &&
              "border-[var(--color-accent-primary)]/40 bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)] hover:bg-[var(--color-accent-muted)] hover:text-[var(--color-accent-hover)]",
          )}
          aria-label={isWrite ? "Write this page with AI" : "Regenerate this page"}
        >
          <Icon
            className={cn(
              "h-3.5 w-3.5",
              isLoading && "animate-spin",
              isWrite && !isLoading && "text-[var(--color-accent-primary)]",
            )}
          />
          <span className="hidden sm:inline">
            {isWrite ? "Write with AI" : "Regenerate"}
          </span>
        </Button>
      )}

      <Dialog
        open={isInProgress}
        onOpenChange={(v) => {
          if (!v) onDialogClose?.();
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {isWrite ? "Writing page with AI" : "Regenerating page"}
            </DialogTitle>
          </DialogHeader>
          {jobSlot}
        </DialogContent>
      </Dialog>
    </>
  );
}

type CascadeOption = { value: RegenerateCascade; label: string; hint: string };

/** Cascade choices phrased for a single page (the reader upgrade). */
const CASCADE_OPTIONS_PAGE: CascadeOption[] = [
  { value: "none", label: "Just this page", hint: "Fastest, cheapest." },
  {
    value: "dependents",
    label: "This page and its summaries",
    hint: "Also refreshes the module, layer, and overview pages that cover it.",
  },
  {
    value: "full",
    label: "This page and all repo-wide pages",
    hint: "Refreshes every page that summarizes the codebase. Costs the most.",
  },
];

/** Cascade choices phrased for a multi-page selection (bulk generation). */
const CASCADE_OPTIONS_SELECTION: CascadeOption[] = [
  { value: "none", label: "Just the selected pages", hint: "Fastest, cheapest." },
  {
    value: "dependents",
    label: "Selected pages and their summaries",
    hint: "Also refreshes the module, layer, and overview pages that cover them.",
  },
  {
    value: "full",
    label: "Selected pages and all repo-wide pages",
    hint: "Refreshes every page that summarizes the codebase. Costs the most.",
  },
];

export interface GenerateConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode?: RegenerateMode;
  /** Page title, shown in the body for context (single-page flavour). */
  pageTitle?: string;
  /** Overrides the dialog title. Defaults to the mode's page-centric heading. */
  title?: string;
  /** Overrides the body sentence. Use for a bulk selection ("Write all 120
   *  template pages…"). When set, `pageTitle` is ignored. */
  description?: ReactNode;
  /** Overrides the confirm button label. Defaults to "Write with AI" /
   *  "Regenerate". */
  confirmLabel?: string;
  /** Phrasing of the cascade options: "page" (single-page reader upgrade) or
   *  "selection" (bulk generation). Defaults to "page". */
  cascadeScope?: "page" | "selection";
  cascade: RegenerateCascade;
  onCascadeChange: (next: RegenerateCascade) => void;
  /** Lazily-fetched estimate for the current cascade. `null` while unknown. */
  estimate?: {
    totalPages: number;
    /** Preformatted cost string (e.g. "$0.01 – $0.02"); omit when no provider. */
    costText?: string | null;
    staleCount?: number;
  } | null;
  /** True while the estimate request is in flight. */
  estimateLoading?: boolean;
  /** A non-fatal estimate error (the launch is still allowed). */
  estimateError?: string | null;
  /** When no provider is configured, the dialog routes to settings instead of
   *  offering to launch. */
  noProvider?: boolean;
  settingsHref?: string;
  onConfirm: () => void;
  /** True once the launch request is in flight. */
  launching?: boolean;
}

export function GenerateConfirmDialog({
  open,
  onOpenChange,
  mode = "write",
  pageTitle,
  title,
  description,
  confirmLabel,
  cascadeScope = "page",
  cascade,
  onCascadeChange,
  estimate,
  estimateLoading = false,
  estimateError,
  noProvider = false,
  settingsHref,
  onConfirm,
  launching = false,
}: GenerateConfirmDialogProps) {
  const isWrite = mode === "write";
  const cascadeOptions =
    cascadeScope === "selection" ? CASCADE_OPTIONS_SELECTION : CASCADE_OPTIONS_PAGE;
  const heading =
    title ?? (isWrite ? "Write this page with AI" : "Regenerate this page");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {isWrite && (
              <Sparkles className="h-4 w-4 text-[var(--color-accent-primary)]" />
            )}
            {heading}
          </DialogTitle>
        </DialogHeader>

        {noProvider ? (
          <div className="space-y-4">
            <p className="text-sm text-[var(--color-text-secondary)]">
              No AI provider is configured for this repository yet. Add a model key
              to write documentation with AI.
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              {settingsHref && (
                <Button asChild size="sm">
                  <a href={settingsHref}>Add a provider key</a>
                </Button>
              )}
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            {description ? (
              <p className="text-sm text-[var(--color-text-secondary)]">
                {description}
              </p>
            ) : (
              pageTitle && (
                <p className="text-sm text-[var(--color-text-secondary)]">
                  {isWrite ? "Write" : "Regenerate"}{" "}
                  <span className="font-medium text-[var(--color-text-primary)]">
                    {pageTitle}
                  </span>{" "}
                  with your configured model.
                </p>
              )
            )}

            <fieldset className="space-y-1.5">
              <legend className="text-xs font-medium text-[var(--color-text-secondary)]">
                What else to update
              </legend>
              {cascadeOptions.map((opt) => (
                <label
                  key={opt.value}
                  className={cn(
                    "flex cursor-pointer items-start gap-2.5 rounded-md border p-2.5 transition-colors",
                    cascade === opt.value
                      ? "border-[var(--color-border-active)] bg-[var(--color-accent-muted)]"
                      : "border-[var(--color-border-default)] hover:bg-[var(--color-bg-inset)]",
                  )}
                >
                  <input
                    type="radio"
                    name="cascade"
                    value={opt.value}
                    checked={cascade === opt.value}
                    onChange={() => onCascadeChange(opt.value)}
                    className="mt-0.5 accent-[var(--color-accent-fill)]"
                  />
                  <span className="min-w-0">
                    <span className="block text-sm text-[var(--color-text-primary)]">
                      {opt.label}
                    </span>
                    <span className="block text-xs text-[var(--color-text-tertiary)]">
                      {opt.hint}
                    </span>
                  </span>
                </label>
              ))}
            </fieldset>

            <div
              aria-live="polite"
              className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-inset)] px-3 py-2 text-xs text-[var(--color-text-secondary)]"
            >
              {estimateLoading ? (
                <span className="inline-flex items-center gap-1.5">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Analyzing the repository to price this accurately…
                </span>
              ) : estimate ? (
                <span>
                  <span className="font-medium text-[var(--color-text-primary)]">
                    {estimate.totalPages}
                  </span>{" "}
                  {estimate.totalPages === 1 ? "page" : "pages"} to write
                  {estimate.costText ? (
                    <>
                      {" · est. "}
                      <span className="font-medium text-[var(--color-text-primary)]">
                        {estimate.costText}
                      </span>
                    </>
                  ) : null}
                  {estimate.staleCount ? (
                    <> · {estimate.staleCount} marked stale</>
                  ) : null}
                </span>
              ) : estimateError ? (
                <span className="text-[var(--color-warning)]">
                  Couldn&apos;t estimate cost. {estimateError}
                </span>
              ) : (
                <span>Cost estimate unavailable.</span>
              )}
            </div>

            <div className="flex justify-end gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onOpenChange(false)}
                disabled={launching}
              >
                Cancel
              </Button>
              <Button size="sm" onClick={onConfirm} disabled={launching}>
                {launching ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : isWrite ? (
                  <>
                    <Sparkles className="mr-1.5 h-3.5 w-3.5" />
                    {confirmLabel ?? "Write with AI"}
                  </>
                ) : (
                  confirmLabel ?? "Regenerate"
                )}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
