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

const MICRO =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

/** The glow band behind the version: orange into plum at low strength,
 *  fading into the dialog ground. Solid stops, checked by contrast-check.py. */
const BAND =
  "linear-gradient(120deg, var(--color-release-glow-1) 0%, var(--color-release-glow-2) 55%, var(--color-release-ground) 100%)";

/** A gentle rise on open; the global reduced-motion rule flattens it. */
const ENTER = "motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 duration-500";

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
    <header
      className="border-b border-[var(--color-border-default)] px-6 pb-6 pt-6 sm:px-8"
      style={{ backgroundImage: BAND }}
    >
      {title}
      <h3
        id={`release-${entry.version}`}
        className={`mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 ${ENTER}`}
      >
        <span className="font-serif text-4xl font-light tracking-tight text-[var(--color-text-primary)] sm:text-5xl">
          {releaseName(entry.version, t("nextRelease"))}
        </span>
        {entry.label && (
          <span className="font-mono text-xs text-[var(--color-text-secondary)]">{entry.label}</span>
        )}
      </h3>
      {entry.version === "unreleased" && (
        <p className="mt-2 text-xs text-[var(--color-text-secondary)]">{t("unreleasedNote")}</p>
      )}
    </header>
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
    <article className="space-y-8" aria-labelledby={`release-${entry.version}`}>
      {entry.summary && (
        <div className="text-[var(--color-text-secondary)] [text-wrap:pretty]">
          <Markdown content={linkPullRequests(entry.summary)} density="narrow" fill />
        </div>
      )}
      {entry.stats && <ReleaseNumbers stats={entry.stats} />}
      {highlights.length > 0 && (
        <Block id={`highlights-${entry.version}`} title={t("highlightsTitle")}>
          <ul className="grid gap-2.5 sm:grid-cols-2">
            {highlights.map((h, i) => (
              <li key={h.id} className={`flex ${ENTER}`}>
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

/** A card only when it opens something; a highlight with no page keeps its
 *  colour as a dot and stays plain. */
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
      <p className="flex items-baseline gap-2 text-[15px] font-medium text-[var(--color-text-primary)]">
        {h.title}
        {fresh && <NewMark />}
      </p>
      {h.blurb && (
        <p className="mt-1 text-xs leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
          {h.blurb}
        </p>
      )}
    </>
  );
  if (href) {
    return (
      <Link
        href={href}
        onClick={onNavigate}
        className="group flex w-full flex-col rounded-lg border border-[var(--color-border-default)] border-l-[3px] bg-[var(--color-release-card)] px-4 py-3 no-underline transition-colors hover:border-[var(--color-border-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
        style={{ borderLeftColor: color }}
      >
        {body}
        <span className="mt-auto inline-flex items-center gap-1 pt-2 text-xs text-[var(--color-accent-primary)] group-hover:underline">
          {t("open")}
          <ArrowRight aria-hidden className="h-3 w-3" />
        </span>
      </Link>
    );
  }
  return (
    <div className="flex w-full gap-3 px-1 py-3">
      <span aria-hidden className="mt-1.5 h-2 w-2 shrink-0 rounded-full" style={{ background: color }} />
      <div className="min-w-0">
        {body}
        {h.href && <p className="mt-1.5 text-xs text-[var(--color-text-tertiary)]">{t("inEachRepo")}</p>}
      </div>
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
      <summary className="flex cursor-pointer list-none items-baseline gap-2 rounded py-2 text-sm text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]">
        <span aria-hidden className="text-[var(--color-text-tertiary)] group-open/section:rotate-90 motion-safe:transition-transform">
          ›
        </span>
        <span className="font-medium">{name}</span>
        <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
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
    <div className="pb-2 pl-4 text-[var(--color-text-secondary)]">
      <Markdown content={content} density="compact" fill />
    </div>
  );
}

interface Figure {
  label: string;
  value: string;
  sub?: string | undefined;
}

/** "By the numbers": large figures, each over a short bar in its series
 *  colour, then the thank-you. No names: the counts carry the credit. */
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
      <dl className="grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-3 lg:grid-cols-5">
        {figures.map((f, i) => (
          <div key={f.label} className={`min-w-0 ${ENTER}`}>
            <span
              aria-hidden
              className="mb-2.5 block h-[3px] w-8 rounded-full"
              style={{ background: seriesColor(i) }}
            />
            <dt className={MICRO}>{f.label}</dt>
            <dd className="mt-1 text-3xl font-semibold tabular-nums tracking-tight text-[var(--color-text-primary)]">
              {f.value}
            </dd>
            {f.sub && (
              <dd className="mt-1 text-xs tabular-nums leading-snug text-[var(--color-text-secondary)]">
                {f.sub}
              </dd>
            )}
          </div>
        ))}
      </dl>
      <p className="text-sm leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
        {first
          ? t("thanksFirst", { count: n(stats.contributors), first: n(first) })
          : t("thanks", { count: n(stats.contributors) })}
      </p>
    </section>
  );
}
