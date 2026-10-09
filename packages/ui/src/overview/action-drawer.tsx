"use client";

import { useCallback, useState, type ElementType, type ReactNode } from "react";
import { ArrowUpRight, BellOff, Check, X } from "lucide-react";
import type {
  ActionDetail,
  ActionStateValue,
  ActionSurface,
  NextAction,
} from "@repowise-dev/types/actions";

import { AiPromptBlock } from "../health/ai-prompt-modal";
import type { AiPromptFlavor } from "../health/ai-prompt-builder";
import { biomarkerLabel } from "../health/biomarker-glossary";
import { EFFORT_LABEL } from "../health/labels";
import { SeverityMark } from "../health/severity-mark";
import type { Severity } from "../health/tokens";
import { AdaptivePanel } from "../shared/adaptive-panel";

const TIER_LABEL: Record<NextAction["tier"], string> = {
  act_now: "Do now",
  plan: "Worth planning",
  improve_signal: "Improve what Repowise can see",
};

/** What the evidence link opens, named for the place it lands. */
const EVIDENCE_LABEL: Record<ActionSurface, string> = {
  file: "Open the file",
  findings: "Open the findings",
  performance: "Open it on Performance",
  security: "Open Security",
  doc_drift: "Open Doc drift",
  dead_code: "Open Dead code",
  decisions: "Open the decision",
  commits: "Open recent commits",
  coverage: "Open Coverage",
};

const BASIS_NOTE: Record<string, string | null> = {
  measured: null,
  inferred: "Inferred from the code graph, not measured.",
  unknown: "Not measured.",
};

export interface ActionDrawerProps {
  action: NextAction | null;
  onClose: () => void;
  /** Where the action's evidence lives; the old row link. */
  evidenceHref: string | null;
  /** A file's own page, for the files the action names. */
  fileHref?: ((path: string) => string | null) | undefined;
  /** Omit to hide the done, snooze and dismiss verbs. */
  onAnswer?: ((state: ActionStateValue, message: string) => void) | undefined;
  /** The agent prompt for an action, as core renders it. Omit to hide the prompt. */
  loadPrompt?: ((action: NextAction, flavor: AiPromptFlavor) => Promise<string>) | undefined;
  /** Show each command's CLI line. Set false for a host with no CLI; agent calls stay. */
  showCliCommands?: boolean | undefined;
  LinkComponent?: ElementType | undefined;
  renderTitle: (title: string) => ReactNode;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h4 className="mb-2 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        {title}
      </h4>
      {children}
    </section>
  );
}

/**
 * One action, opened: why it is on the list, where to work, what done looks
 * like, and a prompt ready for an agent. The row stays a one-line summary; the
 * reading happens here, the same way a performance cause or a file opens on
 * the Code Health pages.
 */
export function ActionDrawer({
  action,
  onClose,
  evidenceHref,
  fileHref,
  onAnswer,
  loadPrompt,
  showCliCommands = true,
  LinkComponent,
  renderTitle,
}: ActionDrawerProps) {
  // Only rendered with both set; stable per action so the block fetches once per flavor.
  const promptSource = useCallback(
    (flavor: AiPromptFlavor) => loadPrompt!(action!, flavor),
    [loadPrompt, action],
  );
  const Link = LinkComponent ?? "a";
  const linkCls =
    "rounded text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]";

  return (
    <AdaptivePanel
      open={action !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      eyebrow={action ? TIER_LABEL[action.tier] : "Next action"}
      title={action ? renderTitle(action.title) : "Next action"}
      widthClassName="md:max-w-[680px]"
    >
      {action ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex-1 space-y-7 overflow-y-auto px-5 py-5">
            <div className="space-y-2">
              {action.tier === "act_now" ? <SeverityMark severity={action.severity} /> : null}
              <p className="text-[15px] leading-relaxed text-[var(--color-text-primary)] [text-wrap:pretty]">
                {renderTitle(action.impact)}
              </p>
              {evidenceHref ? (
                <Link href={evidenceHref} className={`inline-flex items-center gap-1 text-sm ${linkCls}`}>
                  {EVIDENCE_LABEL[action.surface]}
                  <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
                </Link>
              ) : null}
            </div>

            {action.why.length > 0 ? (
              <Section title="Why it is on the list">
                <dl className="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
                  {action.why.map((w) => (
                    <div key={w.label} className="min-w-0">
                      <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                        {w.label}
                      </dt>
                      <dd className="mt-0.5 font-mono text-sm font-medium tabular-nums text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
                        {w.value}
                      </dd>
                      {BASIS_NOTE[w.basis] ? (
                        <dd className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">
                          {BASIS_NOTE[w.basis]}
                        </dd>
                      ) : null}
                    </div>
                  ))}
                </dl>
              </Section>
            ) : null}

            {action.details.length > 0 ? (
              <Section
                title={`Evidence (${action.details_total.toLocaleString()})`}
              >
                <EvidenceList
                  key={action.id}
                  details={action.details}
                  total={action.details_total}
                  fileHref={fileHref}
                  LinkComponent={LinkComponent}
                />
              </Section>
            ) : null}

            {action.details.length === 0 && (action.target.path || action.includes.length > 0) ? (
              <Section title={action.includes.length > 0 ? "Where to start" : "Where"}>
                {action.target.path && action.target.kind !== "decision" ? (
                  <p className="font-mono text-sm text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
                    {action.target.path}
                    {action.target.symbol ? (
                      <span className="text-[var(--color-text-secondary)]">
                        {" :: "}
                        {action.target.symbol.split("::").pop()}
                      </span>
                    ) : null}
                  </p>
                ) : null}
                {action.includes.length > 0 ? (
                  <ol className="mt-1 space-y-1">
                    {action.includes.map((path) => {
                      const href = fileHref?.(path) ?? null;
                      return (
                        <li key={path} className="font-mono text-xs [overflow-wrap:anywhere]">
                          {href ? (
                            <Link href={href} className={linkCls}>
                              {path}
                            </Link>
                          ) : (
                            <span className="text-[var(--color-text-secondary)]">{path}</span>
                          )}
                        </li>
                      );
                    })}
                  </ol>
                ) : null}
                {action.evidence_total > action.includes.length && action.includes.length > 0 ? (
                  <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
                    {`The worst ${action.includes.length} shown; the evidence link has the rest.`}
                  </p>
                ) : null}
              </Section>
            ) : null}

            <Section title="Done when">
              <p className="text-sm text-[var(--color-text-secondary)]">{renderTitle(action.done_when)}</p>
              {action.command ? (
                <pre className="mt-2 overflow-x-auto rounded bg-[var(--color-bg-inset)] px-3 py-2 font-mono text-xs text-[var(--color-text-primary)]">
                  {action.command}
                </pre>
              ) : null}
              <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
                {`${EFFORT_LABEL[action.effort]} effort. `}
                {action.confidence === "high"
                  ? "High confidence in the facts above."
                  : "Worth a check: the facts come from signals that can be wrong for this code."}
              </p>
            </Section>

            {action.commands.length > 0 ? (
              <Section title="Look closer">
                <ul className="space-y-3">
                  {action.commands.map((c) => (
                    <li key={c.purpose} className="space-y-1">
                      <p className="text-sm text-[var(--color-text-secondary)]">{c.purpose}</p>
                      {showCliCommands && c.cli ? (
                        <pre className="overflow-x-auto rounded bg-[var(--color-bg-inset)] px-3 py-1.5 font-mono text-xs text-[var(--color-text-primary)]">
                          {c.cli}
                        </pre>
                      ) : null}
                      {c.mcp ? (
                        <pre className="overflow-x-auto rounded bg-[var(--color-bg-inset)] px-3 py-1.5 font-mono text-xs text-[var(--color-text-secondary)]">
                          <span className="text-[var(--color-text-tertiary)]">MCP </span>
                          {c.mcp}
                        </pre>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </Section>
            ) : null}

            {loadPrompt ? (
              <Section title="Hand it to an agent">
                <div className="-mx-5">
                  <AiPromptBlock bleed="px-5" getPrompt={null} promptSource={promptSource} />
                </div>
              </Section>
            ) : null}
          </div>

          {onAnswer ? (
            <div className="flex flex-wrap items-center gap-2 border-t border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-5 py-3">
              {(
                [
                  ["done", "Mark done", Check, "Marked done. It returns if the facts change."],
                  ["snoozed", "Snooze 14 days", BellOff, "Snoozed for 14 days"],
                  ["dismissed", "Dismiss", X, "Dismissed. It returns if the facts change."],
                ] as const
              ).map(([state, label, Icon, message]) => (
                <button
                  key={state}
                  type="button"
                  onClick={() => {
                    onAnswer(state, message);
                    onClose();
                  }}
                  className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border-default)] px-2.5 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-surface)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
                >
                  <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                  {label}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </AdaptivePanel>
  );
}

/** Rows shown before "Show all"; the payload carries up to 40. */
const EVIDENCE_PREVIEW = 8;

const SEVERITIES = new Set(["critical", "high", "medium", "low"]);

function EvidenceList({
  details,
  total,
  fileHref,
  LinkComponent,
}: {
  details: ActionDetail[];
  total: number;
  fileHref?: ((path: string) => string | null) | undefined;
  LinkComponent?: ElementType | undefined;
}) {
  const [all, setAll] = useState(false);
  const Link = LinkComponent ?? "a";
  const shown = all ? details : details.slice(0, EVIDENCE_PREVIEW);
  return (
    <div className="space-y-2">
      <ul className="divide-y divide-[var(--color-border-default)]">
        {shown.map((d, i) => {
          const href = fileHref?.(d.path) ?? null;
          const where = `${d.path}${d.line ? `:${d.line}` : ""}`;
          return (
            <li key={`${d.path}:${d.line ?? ""}:${d.symbol ?? ""}:${d.marker ?? ""}:${i}`} className="space-y-0.5 py-2">
              <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
                {d.severity && SEVERITIES.has(d.severity) ? (
                  <SeverityMark severity={d.severity as Severity} compact />
                ) : null}
                {href ? (
                  <Link
                    href={href}
                    className="min-w-0 rounded font-mono text-xs text-[var(--color-accent-primary)] [overflow-wrap:anywhere] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
                  >
                    {where}
                  </Link>
                ) : (
                  <span className="min-w-0 font-mono text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
                    {where}
                  </span>
                )}
                {d.symbol ? (
                  <code className="font-mono text-xs text-[var(--color-text-primary)]">
                    {d.symbol.split("::").pop()}
                  </code>
                ) : null}
                {d.marker ? (
                  <span className="text-xs text-[var(--color-text-secondary)]">{biomarkerLabel(d.marker)}</span>
                ) : null}
              </div>
              {d.reason ? (
                <p className="text-xs text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">{d.reason}</p>
              ) : null}
            </li>
          );
        })}
      </ul>
      {details.length > EVIDENCE_PREVIEW || total > details.length ? (
        <p className="flex flex-wrap items-baseline gap-x-3 text-xs text-[var(--color-text-tertiary)]">
          {details.length > EVIDENCE_PREVIEW ? (
            <button
              type="button"
              onClick={() => setAll((a) => !a)}
              className="rounded text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
            >
              {all ? "Show fewer" : `Show all ${details.length.toLocaleString()}`}
            </button>
          ) : null}
          {total > details.length ? (
            <span>{`${(total - details.length).toLocaleString()} more; "Look closer" below lists them.`}</span>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}
