"use client";

/**
 * The section-style settings vocabulary, shared by the global and per-repo
 * settings pages so the two surfaces stop being two design systems.
 *
 * Both pages were built out of `Card`s — seven each, several holding a single
 * field, three of them nesting another bordered box inside. A card means
 * "a discrete object you can act on"; a preference is not that. Grouping is
 * `OverviewSection` (a hairline and vertical rhythm), and inside a section a
 * setting is a row: what it is on the left, the control on the right.
 */

import * as React from "react";
import { Check, Copy } from "lucide-react";
import { Spinner } from "../ui/spinner";
import { cn } from "../lib/cn";

export const SETTINGS_MICRO_LABEL =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

/**
 * One setting. The label column is fixed so a run of rows aligns; below `sm`
 * it stacks, because a 240px label column on a 390px viewport leaves the
 * control nothing.
 */
export function SettingsRow({
  label,
  htmlFor,
  hint,
  children,
  className,
}: {
  label: string;
  /** Associates the label with the control it names. */
  htmlFor?: string;
  /** What the setting does, or what it costs. One line where possible. */
  hint?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-2 border-b border-[var(--color-border-default)] py-4 last:border-b-0",
        "sm:flex-row sm:items-start sm:gap-6",
        className,
      )}
    >
      <div className="min-w-0 sm:w-[240px] sm:shrink-0">
        <label
          className="text-sm font-medium text-[var(--color-text-primary)]"
          {...(htmlFor ? { htmlFor } : {})}
        >
          {label}
        </label>
        {hint && (
          <p className="mt-1 text-xs leading-relaxed text-[var(--color-text-tertiary)] [text-wrap:pretty]">
            {hint}
          </p>
        )}
      </div>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

/** A group of rows. Exists only to close the trailing hairline cleanly. */
export function SettingsRows({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <div className={cn("flex flex-col", className)}>{children}</div>;
}

export type SaveState = "idle" | "saving" | "saved" | "error";

/**
 * The one save affordance.
 *
 * The pages ran two save models at once: three sections wrote on blur with no
 * feedback, and the MCP tool list had an explicit Save button with dirty
 * tracking. Nothing said which half you were in, so the list read as broken to
 * anyone who had learned the page from the fields above it. Everything
 * autosaves now, and says so here.
 *
 * Renders nothing at rest — rule 10. A permanent "Saved" is a badge every row
 * carries.
 */
export function SaveIndicator({
  state,
  error,
  className,
}: {
  state: SaveState;
  error?: string | null;
  className?: string;
}) {
  if (state === "idle") return null;

  return (
    <p
      aria-live="polite"
      className={cn(
        "flex items-center gap-1.5 text-xs",
        state === "error"
          ? "text-[var(--color-error)]"
          : "text-[var(--color-text-tertiary)]",
        className,
      )}
    >
      {state === "saving" && (
        <>
          <Spinner size="xs" />
          Saving…
        </>
      )}
      {state === "saved" && (
        <>
          <Check className="h-3 w-3 text-[var(--color-success)]" aria-hidden />
          Saved
        </>
      )}
      {state === "error" && (error ?? "Could not save")}
    </p>
  );
}

/**
 * A machine-produced string you are meant to copy.
 *
 * Never truncates: the webhook URL is the one string on that page whose entire
 * job is to be read, and an ellipsis in the middle of it reports a width
 * decision as missing content. Long values scroll inside their own box, which
 * is what the layout rules already require of wide content.
 */
export function CopyLine({
  value,
  label,
  className,
}: {
  value: string;
  /** Optional micro-label above the value. */
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = React.useState(false);
  const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  React.useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard can be blocked by permissions; the value is selectable.
    }
  }

  return (
    <div className={cn("min-w-0", className)}>
      {label && <p className={cn(SETTINGS_MICRO_LABEL, "mb-1.5")}>{label}</p>}
      <div className="flex items-start gap-2">
        <code className="min-w-0 flex-1 overflow-x-auto whitespace-pre rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-inset)] px-3 py-2 font-mono text-xs text-[var(--color-text-primary)]">
          {value}
        </code>
        <button
          type="button"
          onClick={copy}
          aria-label={copied ? "Copied" : `Copy ${label ?? "value"}`}
          className="shrink-0 rounded-md p-2 text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
        >
          {copied ? (
            <Check className="h-3.5 w-3.5 text-[var(--color-success)]" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
        </button>
      </div>
    </div>
  );
}

/**
 * Environment variables a choice depends on, as a mono line rather than a
 * dashed box. The box was a bordered container inside a bordered container,
 * for two words and a pip install.
 */
export function EnvVarLine({
  vars,
  note,
  className,
}: {
  vars: string[];
  note?: React.ReactNode;
  className?: string;
}) {
  if (vars.length === 0 && !note) return null;
  return (
    <p
      className={cn(
        "text-xs leading-relaxed text-[var(--color-text-tertiary)]",
        className,
      )}
    >
      {vars.length > 0 && (
        <>
          Needs{" "}
          {vars.map((v, i) => (
            <React.Fragment key={v}>
              {i > 0 && ", "}
              <code className="font-mono text-[var(--color-text-secondary)]">
                {v}
              </code>
            </React.Fragment>
          ))}
          {note ? " · " : " on the server."}
        </>
      )}
      {note}
    </p>
  );
}

/**
 * A green/red state readout.
 *
 * Both pages signalled the same thing two ways: `--color-fresh` /
 * `--color-outdated` in three components and `--color-success` /
 * `--color-error` in two. The freshness pair carries doc-staleness semantics
 * everywhere else in the app, so borrowing it for "the server answered" is the
 * vocabulary drift the fixed-vocabulary table exists to stop. This is the only
 * place either pair is spent on this surface.
 */
export function StatusLine({
  status,
  children,
  className,
}: {
  status: "ok" | "error";
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-xs",
        status === "ok"
          ? "text-[var(--color-success)]"
          : "text-[var(--color-error)]",
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "h-1.5 w-1.5 shrink-0 rounded-full",
          status === "ok"
            ? "bg-[var(--color-success)]"
            : "bg-[var(--color-error)]",
        )}
      />
      {children}
    </span>
  );
}
