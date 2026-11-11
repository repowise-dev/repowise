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

/** One release below its header: summary, the numbers, highlights, then
 *  every change. */
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
    <article className={`space-y-10 ${ENTER}`} aria-labelledby={`release-${entry.version}`}>
      {entry.summary && (
        <div className="max-w-[68ch] text-[var(--color-text-secondary)] [text-wrap:pretty]">
          <Markdown content={linkPullRequests(entry.summary)} density="narrow" fill />
        </div>
      )}
      {entry.stats && <ReleaseNumbers stats={entry.stats} />}
      {highlights.length > 0 && (
        <Block id={`highlights-${entry.version}`} title={t("highlightsTitle")}>
          <ul className="grid gap-3 min-[561px]:grid-cols-2 min-[561px]:[&>li:last-child:nth-child(odd)]:col-span-2">
            {highlights.map((h, i) => (
              <li key={h.id} className="flex">
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

/** Every highlight is the same card, keyed by its series dot; one with a page
 *  opens it, one without says where it lives. */
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
    <>
      <p className={`${MOMENT_BODY} flex items-start gap-3 font-medium text-[var(--color-text-primary)]`}>
        <span className="flex min-w-0 items-center gap-2">
          <span aria-hidden className="size-1.5 shrink-0 rounded-full" style={{ background: color }} />
          {h.title}
        </span>
        {fresh && <NewMark />}
      </p>
      {h.blurb && <p className={`${MOMENT_CAPTION} mt-1 pl-3.5`}>{h.blurb}</p>}
    </>
  );
  const card = `${MOMENT_CARD} flex w-full flex-col p-4`;
  if (href) {
    return (
      <Link
        href={href}
        onClick={onNavigate}
        className={`${card} group no-underline transition-colors hover:border-[var(--color-border-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]`}
      >
        {body}
        <span className="mt-auto inline-flex items-center gap-1 pl-3.5 pt-3 text-xs font-medium text-[var(--color-text-primary)]">
          {t("open")}
          <ArrowRight
            aria-hidden
            className="h-3 w-3 motion-safe:transition-transform motion-safe:duration-150 group-hover:translate-x-0.5"
          />
        </span>
      </Link>
    );
  }
  return (
    <div className={card}>
      {body}
      {h.href && <p className="mt-auto pl-3.5 pt-3 text-xs text-[var(--color-text-tertiary)]">{t("inEachRepo")}</p>}
    </div>
  );
}

function ChangeSection({ name, items }: { name: string; items: string[] }) {
  const t = useTranslations("shell.whatsNewModal");
  // Rendered only once opened: a release's Fixed list runs to a hundred bullets.
  const [opened, setOpened] = useState(false);
  return (
    <details
      className="group/section"
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
  return (
    <div className="pb-3 pl-5 text-[var(--color-text-secondary)]">
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
