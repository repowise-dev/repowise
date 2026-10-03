"use client";

/**
 * One Fix-first item: the change to make, where, why, and what it buys.
 *
 * Collapsed, it is one scan line per fact a person decides on. Expanded, it is
 * the work itself: the steps in order with which ones are mechanical, how to
 * verify the change, the history around the file (muted, never ranked on), and
 * the actions. Every word and number is the payload's; this component chooses
 * only where each one sits.
 */

import { useState, type ElementType } from "react";
import { Check, ChevronDown, Copy } from "lucide-react";
import type { FixItem, FixTier } from "@repowise-dev/types/fix-first";
import type { OpportunityStatus } from "@repowise-dev/types/refactoring";

import { AiPromptButton } from "../ai-prompt-button";
import { CONFIDENCE_LABEL, EFFORT_LABEL, STATUS_LABEL } from "../labels";
import { TIER_LABEL, fixLocation, fixPlanLabel, tierReason } from "./scope";

export type FixTriageStatus = Exclude<OpportunityStatus, "open">;

const TRIAGE: FixTriageStatus[] = ["acknowledged", "resolved", "false_positive"];

/**
 * Tier as a dot and a word. Grayscale on purpose: tier is priority, not health,
 * so it stays off the green/amber/red ramp, and the word carries the meaning.
 */
const TIER_DOT: Record<FixTier, string> = {
  now: "bg-[var(--color-text-primary)]",
  next: "bg-[var(--color-text-tertiary)]",
  later: "border border-[var(--color-text-tertiary)]",
};

const MICRO = "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

const LINK =
  "rounded text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]";

export interface FixFirstItemProps {
  item: FixItem;
  expanded: boolean;
  onToggle: () => void;
  /** The file view for a path. The line rides along for hosts that can scroll to it. */
  fileHref?: ((path: string, line: number | null) => string | undefined) | undefined;
  /** Where "Open plan" lands: the refactoring plan or the performance fix. */
  planHref?: ((item: FixItem) => string | null) | undefined;
  /** Open the shared prompt modal for this item. */
  onAiPrompt?: ((item: FixItem) => void) | undefined;
  /** Triage every finding behind the item. Offered only when it maps to one. */
  onTriage?: ((item: FixItem, status: FixTriageStatus) => Promise<void>) | undefined;
  LinkComponent?: ElementType | undefined;
}

export function FixFirstItem({
  item,
  expanded,
  onToggle,
  fileHref,
  planHref,
  onAiPrompt,
  onTriage,
  LinkComponent,
}: FixFirstItemProps) {
  const Link = LinkComponent ?? "a";
  const panelId = `fix-${item.id}`;
  const location = fixLocation(item);
  const href = fileHref?.(item.target.file_path, item.target.line_start);
  const reason = tierReason(item);

  return (
    <li className="py-4">
      {/* Tier sits in its own column from sm; on a phone it rides above the
          title so the title keeps the width. */}
      <div className="flex min-w-0 flex-col gap-1 sm:flex-row sm:items-start sm:gap-3">
        <span
          className="inline-flex shrink-0 items-center gap-1.5 text-xs text-[var(--color-text-secondary)] sm:mt-[3px] sm:w-14"
          {...(reason ? { title: reason } : {})}
        >
          <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${TIER_DOT[item.tier]}`} />
          {TIER_LABEL[item.tier]}
        </span>

        <div className="min-w-0 flex-1">
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={expanded}
            aria-controls={panelId}
            className="group flex w-full min-w-0 items-start justify-between gap-3 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          >
            <span className="min-w-0 text-[15px] font-semibold leading-snug text-[var(--color-text-primary)] [overflow-wrap:anywhere] [text-wrap:pretty] group-hover:text-[var(--color-accent-primary)]">
              {item.title}
            </span>
            <ChevronDown
              aria-hidden
              className={`mt-0.5 h-4 w-4 shrink-0 text-[var(--color-text-tertiary)] transition-transform ${
                expanded ? "rotate-180" : ""
              }`}
            />
          </button>

          <p className="mt-1 font-mono text-xs [overflow-wrap:anywhere]">
            {href ? (
              <Link href={href} className={LINK}>
                {location}
              </Link>
            ) : (
              <span className="text-[var(--color-text-secondary)]">{location}</span>
            )}
          </p>

          <p className="mt-1.5 max-w-[72ch] text-xs leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
            {item.why}
          </p>

          <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--color-text-tertiary)]">
            <Fact label="Gain" value={item.gain.text} />
            <Fact label="Effort" value={EFFORT_LABEL[item.effort.bucket]} title={item.effort.basis} />
            <Fact
              label="Confidence"
              value={CONFIDENCE_LABEL[item.confidence.level]}
              title={item.confidence.reason}
            />
            <Fact label="Risk" value={item.risk.text} />
          </dl>

          {expanded ? (
            <div id={panelId} className="mt-4 flex flex-col gap-5">
              <Steps item={item} fileHref={fileHref} LinkComponent={LinkComponent} />
              <FixVerify item={item} />
              {item.context.length > 0 ? (
                <section>
                  <h4 className={MICRO}>Context</h4>
                  <dl className="mt-1.5 flex flex-col gap-1 text-xs text-[var(--color-text-tertiary)]">
                    {item.context.map((c) => (
                      <div key={c.label} className="flex min-w-0 flex-wrap gap-x-1.5">
                        <dt>{c.label}:</dt>
                        <dd className="min-w-0 [overflow-wrap:anywhere]">{c.value}</dd>
                      </div>
                    ))}
                  </dl>
                </section>
              ) : null}
              <Actions
                item={item}
                planHref={planHref}
                onAiPrompt={onAiPrompt}
                onTriage={onTriage}
                LinkComponent={LinkComponent}
              />
            </div>
          ) : null}
        </div>
      </div>
    </li>
  );
}

function Fact({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="flex min-w-0 items-baseline gap-1.5" {...(title ? { title } : {})}>
      <dt>{label}</dt>
      <dd className="min-w-0 text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
        {value}
        {title ? <span className="sr-only">. {title}</span> : null}
      </dd>
    </div>
  );
}

function Steps({
  item,
  fileHref,
  LinkComponent,
}: Pick<FixFirstItemProps, "item" | "fileHref" | "LinkComponent">) {
  const Link = LinkComponent ?? "a";
  const { steps, steps_total } = item.action;
  if (steps.length === 0) {
    return (
      <section>
        <h4 className={MICRO}>Do this</h4>
        <p className="mt-1.5 text-sm text-[var(--color-text-secondary)]">{item.action.summary}</p>
      </section>
    );
  }
  return (
    <section>
      <h4 className={MICRO}>Steps</h4>
      <ol className="mt-1.5 flex flex-col gap-2">
        {steps.map((step) => {
          const where = `${step.file_path}${step.line ? `:${step.line}` : ""}`;
          const href = fileHref?.(step.file_path, step.line);
          return (
            <li key={step.order} className="flex min-w-0 gap-2.5 text-sm">
              <span className="w-5 shrink-0 text-right font-mono text-xs tabular-nums leading-5 text-[var(--color-text-tertiary)]">
                {step.order}.
              </span>
              <div className="min-w-0">
                <p className="text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
                  {step.text}
                  <span className="ml-2 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                    {step.mechanical ? "Mechanical" : "Judgment"}
                  </span>
                </p>
                <p className="mt-0.5 font-mono text-xs [overflow-wrap:anywhere]">
                  {href ? (
                    <Link href={href} className={LINK}>
                      {where}
                    </Link>
                  ) : (
                    <span className="text-[var(--color-text-tertiary)]">{where}</span>
                  )}
                </p>
              </div>
            </li>
          );
        })}
      </ol>
      {steps_total > steps.length ? (
        <p className="mt-1.5 text-xs text-[var(--color-text-tertiary)]">
          {`Showing ${steps.length} of ${steps_total} steps; the plan has the rest.`}
        </p>
      ) : null}
    </section>
  );
}

/** How to know the change held: the tests that guard it and the command to run them. */
export function FixVerify({ item }: { item: Pick<FixItem, "verify"> }) {
  const { tests, tests_total, command } = item.verify;
  return (
    <section>
      <h4 className={MICRO}>Verify</h4>
      {tests.length === 0 ? (
        <p className="mt-1.5 text-xs text-[var(--color-text-secondary)]">
          No guarding tests found. Write one that pins the current behaviour before changing it.
        </p>
      ) : (
        <>
          <ul className="mt-1.5 flex flex-col gap-1">
            {tests.map((t) => (
              <li key={t.path} className="min-w-0 text-xs">
                <span className="font-mono text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
                  {t.path}
                </span>
                {t.reason ? (
                  <span className="text-[var(--color-text-tertiary)]">{`: ${t.reason}`}</span>
                ) : null}
              </li>
            ))}
          </ul>
          {tests_total > tests.length ? (
            <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              {`${tests.length} of ${tests_total} tests listed.`}
            </p>
          ) : null}
          {command ? <CommandLine command={command} /> : null}
        </>
      )}
    </section>
  );
}

function CommandLine({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard blocked: the command stays selectable */
    }
  };
  return (
    <div className="mt-2 flex min-w-0 items-start gap-2 rounded bg-[var(--color-bg-inset)] px-2.5 py-1.5">
      <code className="min-w-0 flex-1 font-mono text-xs text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
        {command}
      </code>
      <button
        type="button"
        onClick={() => void copy()}
        aria-label={copied ? "Copied" : "Copy command"}
        className="inline-flex shrink-0 items-center gap-1 rounded text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        <span aria-hidden>{copied ? "Copied" : "Copy"}</span>
      </button>
    </div>
  );
}

function Actions({
  item,
  planHref,
  onAiPrompt,
  onTriage,
  LinkComponent,
}: Pick<FixFirstItemProps, "item" | "planHref" | "onAiPrompt" | "onTriage" | "LinkComponent">) {
  const Link = LinkComponent ?? "a";
  const planLabel = fixPlanLabel(item);
  const href = planLabel ? planHref?.(item) : null;
  const findings = item.source.finding_ids.length;
  const [saved, setSaved] = useState<FixTriageStatus | null>(null);
  const [failed, setFailed] = useState(false);
  const [pending, setPending] = useState(false);

  const triage = async (status: FixTriageStatus) => {
    if (!onTriage) return;
    setPending(true);
    setFailed(false);
    try {
      await onTriage(item, status);
      setSaved(status);
    } catch {
      setFailed(true);
    } finally {
      setPending(false);
    }
  };

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {href ? (
          <Link href={href} className={`text-sm font-medium ${LINK}`}>
            {planLabel}
          </Link>
        ) : null}
        {onAiPrompt ? (
          <AiPromptButton label="Copy prompt for an agent" onClick={() => onAiPrompt(item)} />
        ) : null}
      </div>
      {onTriage && findings > 0 ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs">
          <span className="text-[var(--color-text-tertiary)]">
            {`Mark the ${findings === 1 ? "finding" : `${findings} findings`} behind this as`}
          </span>
          {TRIAGE.map((status) => (
            <button
              key={status}
              type="button"
              disabled={pending || saved === status}
              onClick={() => void triage(status)}
              className="rounded-md border border-[var(--color-border-default)] px-2 py-1 font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] disabled:opacity-50"
            >
              {STATUS_LABEL[status]}
            </button>
          ))}
          <span
            role="status"
            className={failed ? "text-[var(--color-error)]" : "text-[var(--color-text-tertiary)]"}
          >
            {failed
              ? "Could not save that. Nothing changed."
              : saved
                ? `Saved as ${STATUS_LABEL[saved].toLowerCase()}.`
                : ""}
          </span>
        </div>
      ) : null}
    </section>
  );
}
