"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUpRight, Bot, ChevronLeft } from "lucide-react";
import type { AgentPromptFlavor } from "@repowise-dev/types/agent-prompts";
import type {
  FirstLookLivingItem,
  FirstLookLivingReport,
  FirstLookMark,
} from "@repowise-dev/types/first-look";
import { cn } from "../../lib/cn";
import { formatCost, formatDate, formatNumber } from "../../lib/format";
import { renderActionTitle as inline } from "../../overview/next-actions";
import { ContourPattern } from "./contour-pattern";
import { BUTTON, CONTROL, ShareMenu, copyLater, type ShareAction } from "./controls";
import { FIRST_LOOK_COPY_EN, type FirstLookCopy } from "./copy";
import { PAIRED, isClosed, progress, resolvedBySection, stripValue, type Progress } from "./living";
import {
  ReportSection,
  sectionIcon,
  type FirstLookHrefFor,
  type LinkLike,
  type SectionHandlers,
} from "./sections";

/** What a prompt is for: one item, one section, or the whole report. */
export type FirstLookPromptTarget = { item: string } | { section: string } | null;

export interface FirstLookReportProps {
  /** The living report: rebuilt from the index, compared with the first look. */
  report: FirstLookLivingReport;
  hrefFor: FirstLookHrefFor;
  /** Where the top bar's back link goes. */
  overviewHref: string;
  /** The prompt for an item, a section, or the whole report (null). */
  loadPrompt: (target: FirstLookPromptTarget, flavor: AgentPromptFlavor) => Promise<string>;
  /** The flavor copied prompts are worded for. */
  promptFlavor?: AgentPromptFlavor;
  /** Writes a mark through the host; the host refreshes the report after. */
  onMark?: (item: FirstLookLivingItem, mark: FirstLookMark, reason: string | null) => Promise<void>;
  /** The Markdown export's URL; without it the menu leaves the entry out. */
  markdownHref?: string | null;
  /** The command that lists what changed since, where the host has a CLI. */
  sinceCommand?: string | null;
  canRewrite?: boolean;
  onRewrite?: () => void;
  rewriteCost?: number | null;
  rewriting?: boolean;
  copy?: FirstLookCopy;
  LinkComponent?: LinkLike;
}

function useToast(): [string | null, (message: string) => void] {
  const [message, setMessage] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  const show = useCallback((next: string) => {
    setMessage(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setMessage(null), 2200);
  }, []);
  return [message, show];
}

/** Saves *href* as a file; a failed download never navigates the page away. */
function saveAs(href: string, name: string): void {
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  a.click();
}

function download(name: string, text: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  saveAs(url, name);
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

type Text = (item: FirstLookLivingItem) => string;

/** An item's sentence: the report's prose, a resolved item's own, else its rule. */
function itemSentence(item: FirstLookLivingItem, items: Record<string, string> | undefined): string {
  return items?.[item.id] ?? (item as { text?: string | null }).text ?? item.rule;
}

function sectionTitle(key: string, titles: Record<string, string> | undefined, copy: FirstLookCopy): string {
  return titles?.[key] ?? copy.stripTitle[key] ?? key;
}

type MarkHandler = NonNullable<FirstLookReportProps["onMark"]>;

/** Runs the host's mark, and says why when it is refused. */
function reportMarkErrors(onMark: MarkHandler, show: (m: string) => void, fallback: string) {
  return (item: FirstLookLivingItem, mark: FirstLookMark, reason: string | null) => {
    onMark(item, mark, reason).catch((err: unknown) =>
      show(err instanceof Error && err.message ? err.message : fallback),
    );
  };
}

/** What each Share entry does. */
function shareActions({
  report,
  markdownHref,
  copyLink,
  copyReportPrompt,
  preview,
}: {
  report: FirstLookLivingReport;
  markdownHref: string | null | undefined;
  copyLink: () => void;
  copyReportPrompt: () => void;
  preview: () => void;
}): Partial<Record<ShareAction, (() => void) | undefined>> {
  return {
    link: copyLink,
    agent: copyReportPrompt,
    markdown: markdownHref ? () => saveAs(markdownHref, "repo-report.md") : undefined,
    pdf: () => window.print(),
    json: () => download("repo-report.json", `${JSON.stringify(report, null, 2)}\n`, "application/json"),
    preview,
  };
}

function ExportNote({ copy, onExit }: { copy: FirstLookCopy; onExit: () => void }) {
  return (
    <div className="flex flex-wrap items-center justify-center gap-2.5 bg-[var(--color-accent-muted)] p-2 text-[12.5px] print:hidden">
      {copy.exportNote}
      <button type="button" className={BUTTON} onClick={onExit}>
        {copy.exitExport}
      </button>
    </div>
  );
}

function TopBar({
  copy,
  Link,
  overviewHref,
  onHandToAgent,
  onShare,
  hasMarkdown,
}: {
  copy: FirstLookCopy;
  Link: LinkLike;
  overviewHref: string;
  onHandToAgent: () => void;
  onShare: (action: ShareAction) => void;
  hasMarkdown: boolean;
}) {
  return (
    <div className={cn(CONTROL, "sticky top-0 z-[5] bg-[color-mix(in_srgb,var(--color-bg-root)_88%,transparent)] backdrop-blur-sm")}>
      <div className="mx-auto flex max-w-[900px] items-center gap-3 px-4 py-3">
        <Link
          href={overviewHref}
          className="flex items-center gap-1.5 text-[13px] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
        >
          <ChevronLeft aria-hidden className="size-4" />
          {copy.back}
        </Link>
        <span className="flex-1" />
        <button type="button" className={BUTTON} aria-label={copy.handToAgent} onClick={onHandToAgent}>
          <Bot aria-hidden className="size-4" />
          <span className="hidden sm:inline">{copy.handToAgent}</span>
        </button>
        <ShareMenu copy={copy} onAction={onShare} hasMarkdown={hasMarkdown} />
      </div>
    </div>
  );
}

function Footer({
  report,
  copy,
  sinceCommand,
}: {
  report: FirstLookLivingReport;
  copy: FirstLookCopy;
  sinceCommand: string | null | undefined;
}) {
  const { stamp } = report;
  return (
    <footer className="mt-[52px] flex flex-wrap gap-x-[18px] gap-y-1.5 border-t border-[var(--color-border-default)] pt-4 text-[12.5px] text-[var(--color-text-secondary)]">
      <span className="font-mono">{copy.reportAt((stamp.commit ?? "").slice(0, 7))}</span>
      {sinceCommand ? (
        <span>
          {copy.sinceThen} <code className="font-mono text-[0.86em]">{sinceCommand}</code>
        </span>
      ) : null}
    </footer>
  );
}

type Rewrite = { onRewrite: () => void; cost: number | null; running: boolean } | null;

/** Who wrote the wording, and the way to have the model write it again. */
function ProseSource({ report, copy, rewrite }: { report: FirstLookLivingReport; copy: FirstLookCopy; rewrite: Rewrite }) {
  const source = report.prose_source;
  const model = source.writer !== "template";
  const label = model ? copy.rewrite : copy.rewriteWithModel;
  return (
    <div className="flex flex-wrap items-center gap-x-1.5 text-[12.5px] text-[var(--color-text-secondary)]">
      <span title={model ? source.writer : undefined}>
        {model ? copy.wordingByModel(formatDate(source.written_at ?? report.stamp.created_at)) : copy.wordingTemplate}
        {model ? <span className="sr-only"> ({source.writer})</span> : null}
      </span>
      {rewrite ? (
        <>
          <span aria-hidden className={CONTROL}>
            ·
          </span>
          <button
            type="button"
            onClick={rewrite.onRewrite}
            disabled={rewrite.running}
            className={cn(CONTROL, "text-[var(--color-accent-primary)] hover:underline disabled:opacity-60")}
          >
            {rewrite.running ? copy.writing : label}
            {rewrite.cost != null && !rewrite.running ? ` (${copy.approxCost(formatCost(rewrite.cost))})` : null}
          </button>
        </>
      ) : null}
    </div>
  );
}

function Toast({ message }: { message: string | null }) {
  return (
    <div role="status" aria-live="polite">
      {message ? (
        <div className="fixed bottom-[calc(20px+env(safe-area-inset-bottom,0px))] left-1/2 z-20 max-w-[calc(100%-32px)] -translate-x-1/2 rounded-lg bg-[var(--color-action-primary)] px-3.5 py-2 text-[13px] text-[var(--color-text-on-action)] print:hidden">
          {message}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The repo report: what the index finds now, measured against the first look.
 * Every sentence about the repository is `report.prose`, written in core; the
 * page lays it out and wires the host's prompts, marks and exports. Controls
 * carry `first-look-control`, so print and the export view drop all of them.
 */
export function FirstLookReport({
  report,
  hrefFor,
  overviewHref,
  loadPrompt,
  promptFlavor = "generic",
  onMark,
  markdownHref,
  sinceCommand,
  canRewrite = false,
  onRewrite,
  rewriteCost,
  rewriting = false,
  copy = FIRST_LOOK_COPY_EN,
  LinkComponent,
}: FirstLookReportProps) {
  const Link: LinkLike = LinkComponent ?? "a";
  const [toast, showToast] = useToast();
  const [exporting, setExporting] = useState(false);
  const prose = report.prose;

  const copyPrompt = useCallback(
    (target: FirstLookPromptTarget) => {
      void copyLater(loadPrompt(target, promptFlavor)).then((ok) =>
        showToast(ok ? copy.copied : copy.copyFailed),
      );
    },
    [loadPrompt, promptFlavor, showToast, copy],
  );

  const itemText = prose.items;
  const text: Text = useCallback((item) => itemSentence(item, itemText), [itemText]);

  const handlers: SectionHandlers = {
    copy,
    hrefFor,
    Link,
    text,
    onItemPrompt: (item) => copyPrompt({ item: item.id }),
    onSectionPrompt: (section) => copyPrompt({ section }),
    onMark: onMark ? reportMarkErrors(onMark, showToast, copy.copyFailed) : undefined,
  };

  const share = shareActions({
    report,
    markdownHref,
    copyLink: () =>
      void copyLater(Promise.resolve(window.location.href)).then((ok) =>
        showToast(ok ? copy.copied : copy.copyFailed),
      ),
    copyReportPrompt: () => copyPrompt(null),
    preview: () => {
      setExporting(true);
      window.scrollTo(0, 0);
    },
  });

  const resolved = resolvedBySection(report);
  const title = (key: string) => sectionTitle(key, prose.titles, copy);
  const has = (key: string) => report.sections.some((s) => s.key === key);
  const renderSection = (key: string) => {
    const s = report.sections.find((x) => x.key === key);
    return s ? (
      <ReportSection
        key={key}
        section={s}
        title={title(key)}
        intro={prose.sections?.[key]}
        resolved={resolved.get(key) ?? []}
        h={handlers}
      />
    ) : null;
  };
  const paired = PAIRED.every(has);

  return (
    <div data-export={exporting || undefined} className="text-[14.5px] leading-[1.55] text-[var(--color-text-primary)]">
      <style>{"[data-export] .first-look-control{display:none!important}[data-export] [data-decoration]{display:none}"}</style>
      {exporting ? <ExportNote copy={copy} onExit={() => setExporting(false)} /> : null}
      <TopBar
        copy={copy}
        Link={Link}
        overviewHref={overviewHref}
        onHandToAgent={() => copyPrompt(null)}
        onShare={(action) => share[action]?.()}
        hasMarkdown={Boolean(markdownHref)}
      />
      <main className="mx-auto max-w-[900px] px-4 pb-16">
        <Header
          report={report}
          copy={copy}
          rewrite={canRewrite && onRewrite ? { onRewrite, cost: rewriteCost ?? null, running: rewriting } : null}
        />
        <Strip report={report} copy={copy} />
        <Insight report={report} copy={copy} />
        <StartHere report={report} copy={copy} text={text} />
        {report.sections.map((s) => (paired && PAIRED.includes(s.key) ? null : renderSection(s.key)))}
        {paired ? <div className="grid gap-x-10 md:grid-cols-2">{PAIRED.map(renderSection)}</div> : null}
        <Footer report={report} copy={copy} sinceCommand={sinceCommand} />
      </main>
      <Toast message={toast} />
    </div>
  );
}

function ProgressBar({ p }: { p: Progress }) {
  const share = (n: number) => `${p.total ? (n / p.total) * 100 : 0}%`;
  return (
    <span aria-hidden className="flex h-1.5 w-[160px] flex-none overflow-hidden rounded-[3px] bg-[var(--color-bg-inset)]">
      <i className="block h-full bg-[var(--color-success)]" style={{ width: share(p.resolved) }} />
      <i className="block h-full bg-[var(--color-warning)]" style={{ width: share(p.acknowledged) }} />
    </span>
  );
}

/** "14 resolved · 3 acknowledged · 3 new since the first look", with its bar. */
export function ProgressLine({ p, copy, tail }: { p: Progress; copy: FirstLookCopy; tail?: string | undefined }) {
  const b = (n: number) => <b className="font-semibold tabular-nums text-[var(--color-text-primary)]">{formatNumber(n)}</b>;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px] text-[var(--color-text-secondary)]">
      <ProgressBar p={p} />
      <span>
        {b(p.resolved)} {copy.resolved} · {b(p.acknowledged)} {copy.acknowledged} · {b(p.added)} {tail ?? copy.newSince}
      </span>
    </div>
  );
}

function Header({ report, copy, rewrite }: { report: FirstLookLivingReport; copy: FirstLookCopy; rewrite: Rewrite }) {
  const { repo, stamp } = report;
  const first = report.baseline?.stamp ?? null;
  const sha = (stamp.commit ?? "").slice(0, 7);
  return (
    <header className="relative isolate grid gap-3.5 pt-9">
      <ContourPattern sha={stamp.commit} />
      <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-[var(--color-accent-primary)]">{copy.eyebrow}</div>
      <h1 className="m-0 font-serif text-[clamp(28px,4.6vw,38px)] font-semibold leading-[1.15] [overflow-wrap:anywhere] [text-wrap:balance]">
        {repo.name}
      </h1>
      <div className="flex flex-wrap gap-x-3.5 gap-y-1 font-mono text-xs text-[var(--color-text-secondary)]">
        {sha ? <span>{repo.branch ? `${repo.branch} @ ${sha}` : sha}</span> : null}
        <span>{copy.writtenOn(formatDate(stamp.created_at))}</span>
        {repo.files != null ? <span>{copy.files(formatNumber(repo.files))}</span> : null}
        {first ? <span>{copy.firstLookOn(formatDate(first.created_at))}</span> : null}
      </div>
      <ProseSource report={report} copy={copy} rewrite={rewrite} />
      {first ? <ProgressLine p={progress(report)} copy={copy} /> : null}
      {report.prose?.summary ? (
        <p className="m-0 mt-1 max-w-[66ch] text-[16.5px] leading-[1.6]">{inline(report.prose.summary)}</p>
      ) : null}
    </header>
  );
}

function Strip({ report, copy }: { report: FirstLookLivingReport; copy: FirstLookCopy }) {
  if (!report.sections.length) return null;
  return (
    <nav
      aria-label={copy.eyebrow}
      className="mt-[22px] grid grid-cols-2 overflow-hidden rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] min-[441px]:grid-cols-4 md:[grid-template-columns:repeat(var(--n),minmax(0,1fr))]"
      style={{ ["--n" as string]: report.sections.length }}
    >
      {report.sections.map((s) => {
        const Icon = sectionIcon(s.key);
        const hot = s.key === "security" && s.items.some((i) => !isClosed(i));
        return (
          <a
            key={s.key}
            href={`#${s.key}`}
            className="grid gap-[3px] border-b border-r border-[var(--color-border-default)] px-3 pb-[11px] pt-3 hover:bg-[var(--color-bg-inset)] md:border-b-0 md:last:border-r-0"
          >
            <span className={cn("flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]", hot && "text-[var(--color-error)]")}>
              <Icon aria-hidden className="size-4" />
              {copy.stripTitle[s.key] ?? s.key}
            </span>
            <span className="text-xl font-semibold tabular-nums tracking-[-0.01em]">{stripValue(s)}</span>
            <span className="text-[11.5px] leading-[1.3] text-[var(--color-text-tertiary)]">{copy.stripUnit[s.key] ?? ""}</span>
          </a>
        );
      })}
    </nav>
  );
}

function Insight({ report, copy }: { report: FirstLookLivingReport; copy: FirstLookCopy }) {
  const headline = report.prose?.headline;
  if (!headline) return null;
  const lift = report.repo.headline_facts?.find((f) => typeof f.lift === "number")?.lift as number | undefined;
  return (
    <div className="mt-[18px] flex flex-col gap-1 rounded-[10px] bg-[var(--color-accent-muted)] px-3.5 py-3 min-[441px]:flex-row min-[441px]:items-baseline min-[441px]:gap-3">
      {lift != null ? (
        <span className="whitespace-nowrap font-mono font-medium text-[var(--color-accent-primary)]">{lift.toFixed(1)}×</span>
      ) : null}
      <p className="m-0">
        {inline(headline)}{" "}
        <span className="whitespace-nowrap rounded border border-[var(--color-border-default)] px-[5px] py-px font-mono text-[10.5px] uppercase tracking-[0.06em] text-[var(--color-text-secondary)]">
          {copy.measured}
        </span>
      </p>
    </div>
  );
}

function StartHere({ report, copy, text }: { report: FirstLookLivingReport; copy: FirstLookCopy; text: Text }) {
  const byId = new Map(report.sections.flatMap((s) => s.items).map((i) => [i.id, i]));
  const top = report.top.map((id) => byId.get(id)).filter((i): i is FirstLookLivingItem => i != null);
  if (!top.length) return null;
  return (
    <section aria-labelledby="fl-start" className="mt-[34px]">
      <h2 id="fl-start" className="m-0 font-serif text-[21px] font-semibold">
        {copy.startHere}
      </h2>
      <ol className="m-0 mt-3.5 list-none border-t border-[var(--color-border-default)] p-0">
        {top.map((item, n) => {
          const Icon = sectionIcon(item.section);
          return (
            <li
              key={item.id}
              className="grid grid-cols-[22px_minmax(0,1fr)] items-baseline gap-x-2.5 gap-y-1 border-b border-[var(--color-border-default)] py-3 md:grid-cols-[28px_120px_minmax(0,1fr)]"
            >
              <span className="font-mono text-xs text-[var(--color-text-tertiary)]">{n + 1}</span>
              <a
                href={`#${item.id}`}
                className="flex items-center gap-1.5 text-[12.5px] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
              >
                <Icon aria-hidden className="size-4" />
                {copy.stripTitle[item.section] ?? item.section}
              </a>
              <span className="col-start-2 md:col-start-auto">{inline(text(item))}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
