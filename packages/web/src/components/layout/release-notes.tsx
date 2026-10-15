"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { Markdown } from "@repowise-dev/ui/shared/markdown";
import { StatRibbon, type RibbonStat } from "@repowise-dev/ui/stats/stat-ribbon";
import type { ChangelogEntry, ReleaseStats } from "@/lib/api/meta";
import { linkPullRequests, resolveHighlightHref } from "@/lib/release-moment";
import { NewMark } from "./new-mark";

const MICRO =
  "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";

export function releaseName(version: string, nextLabel: string): string {
  return version === "unreleased" ? nextLabel : `v${version}`;
}

/** One release: summary, the numbers, highlights, then every change. */
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
    <article className="space-y-6" aria-labelledby={`release-${entry.version}`}>
      <header className="space-y-2">
        <h3 id={`release-${entry.version}`} className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-lg font-semibold text-[var(--color-text-primary)]">
            {releaseName(entry.version, t("nextRelease"))}
          </span>
          {entry.label && (
            <span className="font-mono text-xs text-[var(--color-text-tertiary)]">{entry.label}</span>
          )}
        </h3>
        {entry.version === "unreleased" && (
          <p className="text-xs text-[var(--color-text-tertiary)]">{t("unreleasedNote")}</p>
        )}
        {entry.summary && (
          <div className="text-[var(--color-text-secondary)]">
            <Markdown content={linkPullRequests(entry.summary)} density="narrow" fill />
          </div>
        )}
      </header>

      {entry.stats && <ReleaseNumbers stats={entry.stats} />}

      {highlights.length > 0 && (
        <section className="space-y-2" aria-labelledby={`highlights-${entry.version}`}>
          <h4 id={`highlights-${entry.version}`} className={MICRO}>
            {t("highlightsTitle")}
          </h4>
          <ul className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
            {highlights.map((h) => {
              const href = resolveHighlightHref(h.href, repoId);
              return (
                <li key={h.id} className="flex items-baseline gap-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <p className="flex items-baseline gap-2 text-sm font-medium text-[var(--color-text-primary)]">
                      {h.title}
                      {fresh && <NewMark />}
                    </p>
                    {h.blurb && (
                      <p className="mt-0.5 text-xs leading-relaxed text-[var(--color-text-secondary)]">
                        {h.blurb}
                      </p>
                    )}
                  </div>
                  {href ? (
                    <Link
                      href={href}
                      onClick={onNavigate}
                      className="shrink-0 rounded text-xs text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
                    >
                      {t("open")}
                      <span className="sr-only">: {h.title}</span>
                    </Link>
                  ) : h.href ? (
                    <span className="shrink-0 text-xs text-[var(--color-text-tertiary)]">
                      {t("inEachRepo")}
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {sections.length > 0 && (
        <section className="space-y-2" aria-labelledby={`all-${entry.version}`}>
          <h4 id={`all-${entry.version}`} className={MICRO}>
            {t("everything")}
          </h4>
          <div className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
            {sections.map((s) => (
              <ChangeSection key={s.name} name={s.name} items={s.items} />
            ))}
          </div>
        </section>
      )}
    </article>
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

/** "By the numbers" as a hairline ribbon, then the thank-you. No names: the
 *  counts carry the credit. */
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

  const cells: RibbonStat[] = [
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
    <section className="space-y-2" aria-label={t("numbersTitle")}>
      <p className={MICRO}>
        {t("numbersTitle")} · {t("numbersSince", { days: stats.days, since: stats.since })}
      </p>
      <StatRibbon stats={cells} />
      <p className="text-xs leading-relaxed text-[var(--color-text-secondary)]">
        {first
          ? t("thanksFirst", { count: n(stats.contributors), first: n(first) })
          : t("thanks", { count: n(stats.contributors) })}
      </p>
    </section>
  );
}
