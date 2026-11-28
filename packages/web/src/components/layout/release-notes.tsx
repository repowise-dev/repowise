"use client";

import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";
import { ArrowRight } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import { Markdown } from "@repowise-dev/ui/shared/markdown";
import { seriesColor } from "@repowise-dev/ui/shared/series";
import type { ChangelogEntry, ChangelogHighlight, ReleaseStats } from "@/lib/api/meta";
import { linkPullRequests, resolveHighlightHref } from "@/lib/release-moment";
import { NewMark } from "./new-mark";
import {
  MOMENT_BODY,
  MOMENT_CAPTION,
  MOMENT_CARD,
  MOMENT_DISPLAY,
  MOMENT_ENTER as ENTER,
  MOMENT_FIGURE,
  MOMENT_MICRO as MICRO,
  MomentBand,
  MomentLabel,
} from "./moment-shell";

export function releaseName(version: string, nextLabel: string): string {
  return version === "unreleased" ? nextLabel : `v${version}`;
}

/** The band at the top of the dialog: the version as the display heading. */
export function ReleaseHeader({
  entry,
  title,
}: {
  entry: ChangelogEntry;
  /** The dialog title, rendered above the version (a `DialogTitle`). */
  title: ReactNode;
}) {
  const t = useTranslations("shell.whatsNewModal");
  return (
    <MomentBand tone="release">
      {title}
      <h3
        id={`release-${entry.version}`}
        className={`mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 ${ENTER}`}
      >
        <span className={MOMENT_DISPLAY}>{releaseName(entry.version, t("nextRelease"))}</span>
        {entry.label && (
          <span className="font-mono text-xs tabular-nums text-[var(--color-text-secondary)]">{entry.label}</span>
        )}
      </h3>
      {entry.version === "unreleased" && <p className={`${MOMENT_CAPTION} mt-2`}>{t("unreleasedNote")}</p>}
    </MomentBand>
  );
}

/** Changes a section can hold and still open on its own; a longer one opens
 *  on a click (a release's Added list runs to forty bullets). */
const OPEN_UP_TO = 5;

/** One release below its header, ordered for a scan: the highlights, the
 *  summary, the numbers, then every change. */
export function ReleaseNotes({
  entry,
  repoId,
  fresh,
  onNavigate,
}: {
  entry: ChangelogEntry;
  /** The repository the reader is in, for repo-scoped highlight routes. */
  repoId: string | null;
  /** Marks the highlights "New": the reader just upgraded to this release. */
  fresh: boolean;
  onNavigate: () => void;
}) {
  const t = useTranslations("shell.whatsNewModal");
  const highlights = entry.highlights ?? [];
  const sections = entry.sections.filter((s) => s.items.length > 0);

  return (
    <article className={`space-y-8 ${ENTER}`} aria-labelledby={`release-${entry.version}`}>
      {highlights.length > 0 && (
        <Block id={`highlights-${entry.version}`} title={t("highlightsTitle")}>
          <ul className={`${MOMENT_CARD} divide-y divide-[var(--color-border-default)] overflow-hidden`}>
            {highlights.map((h, i) => (
              <li key={h.id}>
                <HighlightItem
                  highlight={h}
                  href={resolveHighlightHref(h.href, repoId)}
                  color={seriesColor(i)}
                  fresh={fresh}
                  onNavigate={onNavigate}
                />
              </li>
            ))}
          </ul>
        </Block>
      )}
      {entry.summary && (
        // The summary is support for the highlights above it: caption-sized
        // prose at a short measure, with air between its paragraphs.
        <div className="max-w-[72ch] [&_p]:mb-3 [&_p]:leading-5 [&_p]:text-[var(--color-text-secondary)] [&_p:last-child]:mb-0 [&_p]:[text-wrap:pretty]">
          <Markdown content={linkPullRequests(entry.summary)} density="compact" fill />
        </div>
      )}
      {entry.stats && <ReleaseNumbers stats={entry.stats} />}
      {sections.length > 0 && (
        <Block id={`all-${entry.version}`} title={t("everything")}>
          <div className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
            {sections.map((s) => (
              <ChangeSection key={s.name} name={s.name} items={s.items} />
            ))}
          </div>
        </Block>
      )}
    </article>
  );
}

/** A titled part of the release: micro-label heading, then its content. */
function Block({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section className="space-y-3" aria-labelledby={id}>
      <h4 id={id} className={MICRO}>
        {title}
      </h4>
      {children}
    </section>
  );
}

/** One highlight row, keyed by its series dot: title, blurb, and "Open" when
 *  it has a page; one whose page needs a repository says so. */
function HighlightItem({
  highlight: h,
  href,
  color,
  fresh,
  onNavigate,
}: {
  highlight: ChangelogHighlight;
  /** Resolved route, or null when there is none for this reader. */
  href: string | null;
  color: string;
  fresh: boolean;
  onNavigate: () => void;
}) {
  const t = useTranslations("shell.whatsNewModal");
  const body = (
    <span className="min-w-0 flex-1">
      <span className={`${MOMENT_BODY} flex items-center gap-2 font-medium text-[var(--color-text-primary)]`}>
        <span aria-hidden className="size-1.5 shrink-0 rounded-full" style={{ background: color }} />
        <span className="min-w-0">{h.title}</span>
        {fresh && <NewMark />}
      </span>
      {h.blurb && <span className={`${MOMENT_CAPTION} mt-0.5 block pl-3.5`}>{h.blurb}</span>}
      {!href && h.href && (
        <span className="mt-1 block pl-3.5 text-xs text-[var(--color-text-tertiary)]">{t("inEachRepo")}</span>
      )}
    </span>
  );
  const row = "flex items-start gap-4 px-4 py-3";
  if (href) {
    return (
      <Link
        href={href}
        onClick={onNavigate}
        className={`${row} group no-underline transition-colors hover:bg-[var(--color-bg-elevated)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--color-focus-ring)]`}
      >
        {body}
        <span className="mt-0.5 inline-flex shrink-0 items-center gap-1 text-xs font-medium leading-5 text-[var(--color-text-primary)]">
          {t("open")}
          <ArrowRight
            aria-hidden
            className="h-3 w-3 motion-safe:transition-transform motion-safe:duration-150 group-hover:translate-x-0.5"
          />
        </span>
      </Link>
    );
  }
  return <div className={row}>{body}</div>;
}

/** A section of every change: a short one opens on its own, a long one on a
 *  click. Bullets keep their bold lead-ins. */
function ChangeSection({ name, items }: { name: string; items: string[] }) {
  const t = useTranslations("shell.whatsNewModal");
  const short = items.length <= OPEN_UP_TO;
  // A long section renders only once opened: a Fixed list can run to a
  // hundred bullets.
  const [opened, setOpened] = useState(short);
  return (
    <details
      className="group/section"
      open={short}
      onToggle={(e) => {
        if (e.currentTarget.open) setOpened(true);
      }}
    >
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 rounded text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]">
        <span aria-hidden className="w-3 text-[var(--color-text-tertiary)] group-open/section:rotate-90 motion-safe:transition-transform motion-safe:duration-150">
          ›
        </span>
        <span className={`${MOMENT_BODY} font-medium`}>{name}</span>
        <span className="ml-auto text-xs tabular-nums text-[var(--color-text-tertiary)]">
          {t("sectionCount", { count: items.length })}
        </span>
      </summary>
      {opened && <SectionBody items={items} />}
    </details>
  );
}

function SectionBody({ items }: { items: string[] }) {
  const content = useMemo(
    () => items.map((i) => `- ${linkPullRequests(i)}`).join("\n"),
    [items],
  );
  // Air between bullets: most are a bold lead-in plus two or three lines.
  return (
    <div className="max-w-[80ch] pb-4 pl-5 [&_li]:mb-2 [&_li]:leading-5 [&_li]:text-[var(--color-text-secondary)] [&_strong]:text-[var(--color-text-primary)]">
      <Markdown content={content} density="compact" fill />
    </div>
  );
}

interface Figure {
  label: string;
  value: string;
  sub?: string | undefined;
}

/** "By the numbers": a row of figures, each keyed by its series dot, then the
 *  thank-you. No names: the counts carry the credit. */
export function ReleaseNumbers({ stats }: { stats: ReleaseStats }) {
  const t = useTranslations("shell.whatsNewModal");
  const format = useFormatter();
  const n = (v: number | null | undefined) => format.number(v ?? 0);
  const first = stats.first_time_contributors ?? 0;
  // Kinds with no commits are left out; "0 performance changes" says nothing.
  const kt = stats.types;
  const kinds = (
    [
      ["statFeatures", kt.features],
      ["statPerf", kt.perf],
      ["statRefactors", kt.refactors],
      ["statDocs", kt.docs],
    ] as const
  )
    .filter(([, count]) => !!count)
    .map(([key, count]) => t(key, { count: n(count) }));
  const kindsSub = kinds.length ? format.list(kinds, { type: "conjunction" }) : undefined;

  const figures: Figure[] = [
    { label: t("statPrs"), value: n(stats.prs), sub: t("statPrsSub", { count: n(stats.issues_closed) }) },
    {
      label: t("statContributors"),
      value: n(stats.contributors),
      sub: first ? t("statContributorsSub", { count: n(first) }) : undefined,
    },
    {
      label: t("statFiles"),
      value: n(stats.files_changed),
      sub: t("statFilesSub", { added: n(stats.lines_added), removed: n(stats.lines_removed) }),
    },
    ...(kt.fixes ? [{ label: t("statFixes"), value: n(kt.fixes), sub: kindsSub }] : []),
    {
      label: t("statTests"),
      value: n(stats.test_files_touched),
      sub: t("statTestsSub", { count: n(stats.test_files_new) }),
    },
  ];

  return (
    <section className="space-y-4" aria-label={t("numbersTitle")}>
      <p className={MICRO}>
        {t("numbersTitle")} · {t("numbersSince", { days: stats.days, since: stats.since })}
      </p>
      <dl className={`${MOMENT_CARD} grid grid-cols-2 gap-x-6 gap-y-6 p-4 min-[561px]:grid-cols-3`}>
        {figures.map((f, i) => (
          <div key={f.label} className="min-w-0">
            <dt>
              <MomentLabel color={seriesColor(i)}>{f.label}</MomentLabel>
            </dt>
            <dd className={`${MOMENT_FIGURE} mt-2`}>{f.value}</dd>
            {f.sub && <dd className={`${MOMENT_CAPTION} mt-1 tabular-nums`}>{f.sub}</dd>}
          </div>
        ))}
      </dl>
      <p className={`${MOMENT_BODY} max-w-[68ch] text-[var(--color-text-secondary)] [text-wrap:pretty]`}>
        {first
          ? t("thanksFirst", { count: n(stats.contributors), first: n(first) })
          : t("thanks", { count: n(stats.contributors) })}
      </p>
    </section>
  );
}
