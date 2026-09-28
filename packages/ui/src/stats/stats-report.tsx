import * as React from "react";
import type { StatsHighlights } from "@repowise-dev/types/stats";
import { PageLede } from "../shared/page-lede";
import { formatAgeDays, formatDate, formatNumber } from "../lib/format";
import { ArrivalsTimeline } from "./arrivals-timeline";
import { ChronotypeList } from "./chronotype-list";
import { ChurnLedger } from "./churn-ledger";
import { OriginBlock } from "./origin-block";
import { PunchCard } from "./punch-card";
import { RecordsList, type RecordLinks } from "./records-list";
import { NLOC_HINT } from "./stat-callout";
import { StatRibbon, type RibbonStat } from "./stat-ribbon";
import { DEFAULT_WEEKEND_PRESET } from "./weekend";

export interface StatsReportProps extends RecordLinks {
  data: StatsHighlights;
  /** Weekday indices (0 = Monday) the reader counts as the weekend. */
  weekendDays?: readonly number[];
  contributorsHref?: string | undefined;
}

function Section({
  title,
  lede,
  children,
}: {
  title: string;
  lede?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section aria-label={title} className="flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <h2 className="text-[22px] font-semibold text-[var(--color-text-primary)]">{title}</h2>
        {lede && (
          <p className="max-w-[72ch] text-xs text-[var(--color-text-secondary)]">{lede}</p>
        )}
      </div>
      {children}
    </section>
  );
}

function monthLabel(key: string): string {
  const [y, m] = key.split("-");
  // Built from parts so the browser's offset can never nudge it across a month.
  return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString("en-US", {
    month: "short",
    year: "numeric",
  });
}

function hoursLabel(hours: number): string {
  if (hours < 48) return `${hours} hours`;
  return formatAgeDays(Math.floor(hours / 24));
}

function span(start: string, end: string): string {
  return `${formatDate(start)} to ${formatDate(end)}`;
}

function languageMix(data: StatsHighlights): string | undefined {
  const langs = data.scale.languages ?? [];
  const total = langs.reduce((a, l) => a + l.file_count, 0);
  if (total === 0) return undefined;
  return langs
    .slice(0, 2)
    .map((l) => `${l.language} ${Math.round((l.file_count / total) * 100)}%`)
    .join(", ");
}

function identityStats(data: StatsHighlights): RibbonStat[] {
  const { scale, origin } = data;
  return [
    { label: "Files", value: formatNumber(scale.file_count) },
    { label: "Symbols", value: formatNumber(scale.symbol_count) },
    { label: "Modules", value: formatNumber(scale.module_count) },
    {
      label: "Languages",
      value: formatNumber(scale.language_count),
      sub: languageMix(data),
      hint: "Programming languages by file count. Data and markup formats such as JSON, YAML and Markdown are not counted.",
    },
    { label: "Commits", value: formatNumber(origin.total_commits) },
  ];
}

function rhythmStats(rhythm: StatsHighlights["rhythm"]): RibbonStat[] {
  const { longest_streak: streak, longest_silence: silence, busiest_day: day } = rhythm;
  const v = rhythm.velocity;
  const month = rhythm.busiest_month;
  const cells: (RibbonStat | null)[] = [
    streak
      ? { label: "Longest streak", value: `${formatNumber(streak.days)} days`, sub: span(streak.start, streak.end) }
      : null,
    silence
      ? {
          label: "Longest silence",
          value: hoursLabel(silence.hours),
          sub: span(silence.start, silence.end),
          hint: "The longest gap between two consecutive commits.",
        }
      : null,
    day
      ? { label: "Busiest day", value: `${formatNumber(day.commits)} commits`, sub: formatDate(day.date) }
      : null,
    rhythm.code_half_life_days != null
      ? {
          label: "Code half-life",
          value: `${formatNumber(rhythm.code_half_life_days)} days`,
          sub: "half the files have gone unchanged this long",
          hint: "Median time since each file was last changed, measured from the newest commit.",
        }
      : null,
    v && v.pct_change != null
      ? {
          label: "Momentum",
          value: `${v.pct_change >= 0 ? "+" : ""}${v.pct_change}%`,
          sub: `${formatNumber(v.recent_90d)} commits in 90 days vs ${formatNumber(v.prior_90d)} before`,
          hint: "The 90 days ending at the newest commit, against the 90 before it.",
        }
      : month
        ? { label: "Busiest month", value: monthLabel(month.month), sub: `${formatNumber(month.total)} commits` }
        : null,
  ];
  return cells.filter((s): s is RibbonStat => s !== null);
}

function peopleStats(people: StatsHighlights["people"], contributorsHref: string | undefined): RibbonStat[] {
  const tf = people.truck_factor;
  const cells: (RibbonStat | null)[] = [
    {
      label: "Contributors",
      value: formatNumber(people.contributor_count),
      sub: "people who have committed",
      ...(contributorsHref ? { href: contributorsHref } : {}),
    },
    { label: "Owners", value: formatNumber(people.owner_count), sub: "own at least one file" },
    tf != null
      ? {
          label: "Truck factor",
          value: formatNumber(tf),
          sub: tf === 1 ? "person owns most of the code" : "people own most of the code",
          hint: "The fewest primary owners who together hold more than half the owned files.",
        }
      : null,
    {
      label: "Single-owner files",
      value: formatNumber(people.single_owner_files),
      sub: people.tracked_files ? `of ${formatNumber(people.tracked_files)} files` : undefined,
      hint: "Files where one author made at least 80% of the commits.",
    },
    {
      label: "Knowledge silos",
      value: formatNumber(people.silo_count),
      sub: "top-level folders with one dominant owner",
      hint: "Top-level folders where one person is the primary owner of more than 80% of the files.",
    },
  ];
  return cells.filter((s): s is RibbonStat => s !== null);
}

function windowSentence(data: StatsHighlights): string | undefined {
  const sample = data.rhythm.window;
  if (!sample?.first_at || !sample.last_at) return undefined;
  const days = `across ${formatNumber(data.rhythm.active_days)} active days`;
  const range = span(sample.first_at, sample.last_at);
  return sample.complete
    ? `Every commit from ${range}, ${days}.`
    : `Drawn from the latest ${formatNumber(sample.commits)} of ${formatNumber(
        data.origin.total_commits,
      )} commits, ${range}, ${days}.`;
}

function SizeLede({ data }: { data: StatsHighlights }) {
  const { scale } = data;
  const testNloc = scale.test_nloc ?? 0;
  const sourceNloc = scale.total_nloc - testNloc;
  return (
    <PageLede
      label="Lines of code"
      labelHint={NLOC_HINT}
      value={formatNumber(scale.total_nloc)}
      band={{ label: scale.size_class.name }}
      layout="beside"
    >
      <p>
        {data.repo.name ? `${data.repo.name} is a ${scale.size_class.name}. ` : ""}
        {scale.size_class.blurb}
        {testNloc > 0 && sourceNloc > 0 && (
          <>
            {" "}For every 100 lines of source there are{" "}
            <strong className="font-semibold text-[var(--color-text-primary)]">
              {Math.round((testNloc / sourceNloc) * 100)} lines of tests
            </strong>
            .
          </>
        )}
      </p>
    </PageLede>
  );
}

/**
 * The whole Stats page: what the repo is, its records, when the work happens,
 * and who does it. One scroll, because every section is already in the single
 * payload and tabs only hid most of it.
 *
 * Shared by every host; the host supplies the weekend preference and whatever
 * routes it has.
 */
export function StatsReport({
  data,
  weekendDays = DEFAULT_WEEKEND_PRESET.days,
  contributorsHref,
  ...links
}: StatsReportProps) {
  const { origin, churn, rhythm, people, records } = data;
  const sample = rhythm.window;
  const partial = sample != null && !sample.complete;
  const rhythmCells = rhythmStats(rhythm);

  return (
    <div className="flex flex-col gap-12">
      <div className="flex flex-col gap-8">
        <SizeLede data={data} />
        <StatRibbon stats={identityStats(data)} />
        <OriginBlock data={origin} />
        {churn && <ChurnLedger data={churn} />}
      </div>

      <Section
        title="Records"
        lede="The biggest, longest and most tangled things in the codebase. The length, naming and patch records leave test code out."
      >
        <RecordsList
          records={records}
          commitScope={partial ? `latest ${formatNumber(sample.commits)}` : undefined}
          {...links}
        />
      </Section>

      <Section title="When the work happens" lede={windowSentence(data)}>
        <PunchCard
          data={rhythm.punch_card}
          weekendDays={weekendDays}
          firstCommitAt={sample?.first_at ?? origin.first_commit_at}
          lastCommitAt={sample?.last_at ?? origin.last_commit_at}
        />
        {rhythmCells.length > 0 && <StatRibbon stats={rhythmCells} />}
      </Section>

      <Section title="Who does it">
        <StatRibbon stats={peopleStats(people, contributorsHref)} LinkComponent={links.LinkComponent} />
        <ChronotypeList people={people.chronotypes} weekendDays={weekendDays} />
        {people.chronotypes.length === 0 && rhythm.punch_card.timezone_mode === "utc" && (
          <p className="text-xs text-[var(--color-text-secondary)]">
            Commit-hour habits need each commit&apos;s local time, which this index does not
            carry.
          </p>
        )}
        <ArrivalsTimeline arrivals={people.arrivals} partialSince={partial ? sample.first_at : null} />
      </Section>
    </div>
  );
}
