"use client";

import { useCallback, useMemo, type ReactNode } from "react";
import type { AgentPromptFlavor } from "@repowise-dev/types/agent-prompts";
import {
  FIRST_LOOK_SECTION_CAP,
  type FirstLookItem,
  type FirstLookReport as Report,
  type FirstLookSection,
  type FirstLookStaleness,
} from "@repowise-dev/types/first-look";
import { useAiPromptAction } from "../../health/ai-prompt-action";
import { fileChatContext } from "../../health/ai-prompt-modal";
import { AiPromptButton } from "../../health/ai-prompt-button";
import { cn } from "../../lib/cn";
import { renderActionTitle as inline } from "../../overview/next-actions";
import { formatCost, formatDate, formatNumber } from "../../lib/format";
import {
  FirstLookItemRow,
  MONO_CAPS,
  SECTION_ROUTE,
  shortSha,
  type FirstLookHrefFor,
  type ItemState,
  type LinkLike,
} from "./first-look-parts";

export interface FirstLookReportProps {
  report: Report;
  /** The report against the index now; fixed and changed items are marked. */
  staleness?: FirstLookStaleness | null;
  /** The shell's URL for a route key; null hides the link. */
  hrefFor: FirstLookHrefFor;
  /** The flavor `reportPrompt` was rendered in. */
  promptFlavor: AgentPromptFlavor;
  /** "Work through this report", already rendered for `promptFlavor`. */
  reportPrompt?: string | null;
  /** An item's prompt, or the whole report's when `itemId` is null, for any flavor. */
  loadPrompt: (itemId: string | null, flavor: AgentPromptFlavor) => Promise<string>;
  /** A model can write the report: shows "Write it with AI". */
  canRewrite?: boolean;
  onRewrite?: () => void;
  /** What one rewrite costs in USD, when known. */
  rewriteCost?: number | null;
  /** A rewrite is running. */
  rewriting?: boolean;
  LinkComponent?: LinkLike;
}

/**
 * The first-look report page: what the first index found, most urgent first.
 * Every sentence is `report.prose`, written once in core; this component only
 * lays it out with the item's chips, marks and actions. Routes, prompts and
 * the rewrite are the host's, through props, so every shell renders the same page.
 */
export function FirstLookReport({
  report,
  staleness,
  hrefFor,
  promptFlavor,
  reportPrompt,
  loadPrompt,
  canRewrite = false,
  onRewrite,
  rewriteCost,
  rewriting = false,
  LinkComponent,
}: FirstLookReportProps) {
  const Link: LinkLike = LinkComponent ?? "a";
  const { openPrompt, promptModal } = useAiPromptAction();
  const prose = report.prose ?? {};
  const itemText = prose.items ?? {};
  const titles = prose.titles ?? {};
  const title = (key: string) => titles[key] ?? key;
  const states = useMemo(() => itemStates(staleness), [staleness]);

  const promptItem = useCallback(
    (item: FirstLookItem) => {
      const path = item.locations[0]?.path ?? null;
      const chatContext = fileChatContext(path);
      openPrompt({
        title: "Fix with your agent",
        filePath: path,
        promptSource: (flavor) => loadPrompt(item.id, flavor),
        ...(chatContext ? { chatContext } : {}),
      });
    },
    [openPrompt, loadPrompt],
  );
  const promptReport = useCallback(
    () =>
      openPrompt({
        title: "Work through this report",
        promptSource: (flavor) =>
          flavor === promptFlavor && reportPrompt
            ? Promise.resolve(reportPrompt)
            : loadPrompt(null, flavor),
      }),
    [openPrompt, loadPrompt, promptFlavor, reportPrompt],
  );

  const row = (item: FirstLookItem) => (
    <FirstLookItemRow
      key={item.id}
      item={item}
      text={itemText[item.id]}
      state={states.get(item.id) ?? null}
      reportCommit={report.stamp.commit}
      hrefFor={hrefFor}
      Link={Link}
      onPrompt={promptItem}
    />
  );

  return (
    <div className="mx-auto max-w-[880px] pb-20 text-[15px] leading-[1.55] text-[var(--color-text-primary)]">
      <Header
        report={report}
        staleness={staleness ?? null}
        canRewrite={canRewrite}
        onRewrite={onRewrite}
        rewriteCost={rewriteCost ?? null}
        rewriting={rewriting}
        onReportPrompt={promptReport}
      />
      <Figures report={report} />
      {prose.summary ? <p className="mt-5 max-w-[68ch] text-base leading-[1.65]">{inline(prose.summary)}</p> : null}
      {prose.headline ? (
        <p className="mt-2.5 text-xs text-[var(--color-text-secondary)]">
          {inline(prose.headline)}{" "}
          <span className={cn(MONO_CAPS, "ml-1 rounded border border-[var(--color-border-default)] px-1.5 py-0.5")}>
            measured
          </span>
        </p>
      ) : null}
      <DoFirst report={report} text={itemText} title={title} />
      {report.sections.map((section) => (
        <Section
          key={section.key}
          section={section}
          title={title(section.key)}
          intro={prose.sections?.[section.key]}
          hrefFor={hrefFor}
          Link={Link}
        >
          {section.items.slice(0, FIRST_LOOK_SECTION_CAP).map(row)}
        </Section>
      ))}
      <TurnOnMore report={report} />
      <footer className="mt-12 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-[var(--color-text-tertiary)]">
        <span>Regenerate: <code className="font-mono">repowise report first-look --llm</code></span>
        <span>Since then: <code className="font-mono">repowise report first-look --since-last</code></span>
      </footer>
      {promptModal}
    </div>
  );
}

function itemStates(staleness: FirstLookStaleness | null | undefined): Map<string, ItemState> {
  const states = new Map<string, ItemState>();
  if (!staleness) return states;
  for (const id of staleness.changed) states.set(id, "changed");
  for (const id of staleness.fixed) states.set(id, "fixed");
  return states;
}

function Header({
  report,
  staleness,
  canRewrite,
  onRewrite,
  rewriteCost,
  rewriting,
  onReportPrompt,
}: {
  report: Report;
  staleness: FirstLookStaleness | null;
  canRewrite: boolean;
  onRewrite: (() => void) | undefined;
  rewriteCost: number | null;
  rewriting: boolean;
  onReportPrompt: () => void;
}) {
  const { stamp, repo } = report;
  const commit = shortSha(stamp.commit);
  return (
    <header>
      <div className={cn(MONO_CAPS, "text-[var(--color-text-tertiary)]")}>First look</div>
      <h1 className="mb-2 mt-1.5 text-[26px] font-semibold leading-tight tracking-[-0.01em] [overflow-wrap:anywhere] sm:text-[32px]">
        {repo.name}
      </h1>
      <div className="flex flex-wrap gap-x-3.5 gap-y-1 font-mono text-xs text-[var(--color-text-secondary)]">
        {commit ? <span>{repo.branch ? `${repo.branch} @ ${commit}` : commit}</span> : null}
        <span>written {formatDate(stamp.created_at)}</span>
        {repo.files != null ? <span>{formatNumber(repo.files)} files</span> : null}
      </div>
      <div className="mt-3.5 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-[var(--color-text-secondary)]">
        <Writer generator={stamp.generator} cost={stamp.cost} />
        {canRewrite && onRewrite ? (
          <button
            type="button"
            onClick={onRewrite}
            disabled={rewriting}
            className="rounded font-medium text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)] disabled:opacity-60"
          >
            {rewriting ? "Writing..." : "Write it with AI"}
            {rewriteCost != null && !rewriting ? `, about ${formatCost(rewriteCost)}` : null}
          </button>
        ) : null}
        <AiPromptButton label="Work through this report" onClick={onReportPrompt} className="sm:ml-auto" />
      </div>
      {staleness?.behind ? <StaleNote staleness={staleness} /> : null}
    </header>
  );
}

function Writer({ generator, cost }: { generator: string; cost: number | null }) {
  const template = generator === "template";
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span
        className={cn(
          MONO_CAPS,
          "rounded px-1.5 py-0.5",
          template
            ? "bg-[var(--color-action-soft)] text-[var(--color-text-secondary)]"
            : "bg-[var(--color-model-muted)] text-[var(--color-model)]",
        )}
      >
        {template ? "template" : "model"}
      </span>
      <span>
        {template ? "Template" : `Written by ${generator}${cost != null ? `, ${formatCost(cost)}` : ""}`}
      </span>
    </span>
  );
}

function StaleNote({ staleness }: { staleness: FirstLookStaleness }) {
  const parts = [
    staleness.fixed.length ? `${formatNumber(staleness.fixed.length)} fixed` : null,
    staleness.changed.length ? `${formatNumber(staleness.changed.length)} changed` : null,
    staleness.new.length ? `${formatNumber(staleness.new.length)} new, not in this report` : null,
  ].filter(Boolean);
  return (
    <p
      role="status"
      className="mt-4 border-l-2 border-[var(--color-warning)] bg-[var(--color-bg-surface)] px-3 py-2.5 text-xs text-[var(--color-text-secondary)]"
    >
      Written at <span className="font-mono">{shortSha(staleness.report_commit)}</span>. The index
      is now at <span className="font-mono">{shortSha(staleness.current_commit)}</span>
      {parts.length ? `: ${parts.join(", ")}.` : "."}
    </p>
  );
}

interface Figure {
  key: string;
  value: ReactNode;
  label: string;
  alert?: boolean;
}

/** Headline numbers, each with its unit. Shown only where the report holds them. */
function figures(report: Report): Figure[] {
  const out: Figure[] = [];
  const health = report.repo.health;
  if (health) {
    out.push({ key: "health", value: <>{health.score.toFixed(1)}<Unit>/10</Unit></>, label: `Health, ${health.band_label}` });
  }
  const security = report.sections.find((s) => s.key === "security");
  if (security) {
    const n = security.items.length;
    out.push({ key: "security", value: formatNumber(n), label: "security findings to act on", alert: n > 0 });
  }
  const dead = report.sections.find((s) => s.key === "dead_code")?.facts;
  if (typeof dead?.safe_exports === "number") {
    out.push({ key: "dead_code", value: formatNumber(dead.safe_exports), label: "exports safe to delete" });
  }
  const tests = report.sections.find((s) => s.key === "tests")?.facts;
  if (typeof tests?.line_pct === "number") {
    out.push({ key: "tests", value: <>{tests.line_pct.toFixed(1)}<Unit>%</Unit></>, label: "line coverage, measured" });
  }
  return out;
}

const Unit = ({ children }: { children: ReactNode }) => (
  <small className="text-xs font-normal text-[var(--color-text-tertiary)]">{children}</small>
);

function Figures({ report }: { report: Report }) {
  const shown = figures(report);
  if (!shown.length) return null;
  return (
    <div
      aria-label="Headline figures"
      className="mb-2 mt-7 grid grid-cols-2 border-y border-[var(--color-border-default)] sm:grid-cols-4"
    >
      {shown.map((f, i) => (
        <div
          key={f.key}
          className={cn(
            "min-w-0 py-3.5 pr-3",
            i > 0 && "sm:border-l sm:border-[var(--color-border-default)] sm:pl-3.5",
            i % 2 === 1 && "border-l border-[var(--color-border-default)] pl-3.5",
            i >= 2 && "border-t border-[var(--color-border-default)] sm:border-t-0",
          )}
        >
          <div className={cn("text-[22px] font-semibold tabular-nums", f.alert && "text-[var(--color-error)]")}>
            {f.value}
          </div>
          <div className="text-xs text-[var(--color-text-secondary)]">{f.label}</div>
        </div>
      ))}
    </div>
  );
}

function DoFirst({
  report,
  text,
  title,
}: {
  report: Report;
  text: Record<string, string>;
  title: (key: string) => string;
}) {
  // Only the rows a section shows have an anchor to go to.
  const shown = new Map(
    report.sections.flatMap((s) => s.items.slice(0, FIRST_LOOK_SECTION_CAP)).map((i) => [i.id, i]),
  );
  const items = report.top.map((id) => shown.get(id)).filter((i): i is FirstLookItem => i != null);
  if (!items.length) return null;
  return (
    <section aria-labelledby="fl-first" className="mt-7">
      <h2 id="fl-first" className="mb-1.5 text-lg font-semibold">Do these first</h2>
      <ol>
        {items.map((item, n) => (
          <li
            key={item.id}
            className="grid grid-cols-[22px_minmax(0,1fr)] items-baseline gap-x-2.5 gap-y-1 border-t border-[var(--color-border-default)] py-3 sm:grid-cols-[28px_minmax(0,1fr)_auto]"
          >
            <span className="font-mono text-xs text-[var(--color-accent-primary)]">{n + 1}</span>
            <div className="min-w-0">
              <span className={cn(MONO_CAPS, "block text-[var(--color-text-tertiary)]")}>
                {title(item.section).split(":")[0]}
              </span>
              {inline(text[item.id] ?? item.rule)}
            </div>
            <a
              href={`#${item.id}`}
              aria-label={`Go to item: ${text[item.id] ?? item.rule}`}
              className="col-start-2 whitespace-nowrap text-xs text-[var(--color-accent-primary)] hover:underline sm:col-start-3">
              Go to item
            </a>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Section({
  section,
  title,
  intro,
  hrefFor,
  Link,
  children,
}: {
  section: FirstLookSection;
  title: string;
  intro: string | undefined;
  hrefFor: FirstLookHrefFor;
  Link: LinkLike;
  children: ReactNode;
}) {
  const route = SECTION_ROUTE[section.key];
  const href = route ? hrefFor(route, {}) : null;
  const more = section.items.length - FIRST_LOOK_SECTION_CAP;
  const heading = `fl-sec-${section.key}`;
  return (
    <section aria-labelledby={heading} data-section={section.key} className="mt-11">
      <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-[var(--color-border-default)] pb-2">
        <h2 id={heading} className="text-xl font-semibold sm:text-[22px]">{title}</h2>
        {href ? (
          <Link href={href} aria-label={`Open ${title} in the app`} className="font-mono text-xs text-[var(--color-text-tertiary)] hover:text-[var(--color-accent-primary)]">
            open in app
          </Link>
        ) : null}
      </div>
      {intro ? <p className="mb-1 mt-3 max-w-[68ch] text-[var(--color-text-secondary)]">{inline(intro)}</p> : null}
      {children}
      {more > 0 ? (
        <p className="mt-2.5 text-xs text-[var(--color-text-tertiary)]">
          {href ? (
            <Link href={href} className="text-[var(--color-accent-primary)] hover:underline">
              {formatNumber(more)} more in the app
            </Link>
          ) : (
            `${formatNumber(more)} more in the app`
          )}
        </p>
      ) : null}
    </section>
  );
}

function TurnOnMore({ report }: { report: Report }) {
  if (!report.setup.length) return null;
  const setup = report.prose.setup ?? {};
  return (
    <section aria-labelledby="fl-more" className="mt-12 border-t border-[var(--color-border-default)] pt-3">
      <h2 id="fl-more" className="mb-1 text-lg font-semibold">Turn on more</h2>
      <ul className="mt-1.5 list-disc pl-[18px] text-[var(--color-text-secondary)]">
        {report.setup.map((step) => (
          <li key={step.action_id} className="my-1.5">
            {inline(setup[step.action_id] ?? step.title)}
            {step.command ? (
              <>
                {" "}
                <code className="rounded bg-[var(--color-bg-inset)] px-1.5 py-px font-mono text-[0.85em] text-[var(--color-text-primary)] [overflow-wrap:anywhere]">
                  {step.command}
                </code>
              </>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
