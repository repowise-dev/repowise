import * as React from "react";
import type { StatsHighlights, StatsVelocity } from "@repowise-dev/types/stats";
import { formatBytes, formatDate, formatDateWith, formatNumber } from "../lib/format";
import { repoArchetype } from "./archetype";
import { ArrivalsTimeline } from "./arrivals-timeline";
import { ChronotypeList } from "./chronotype-list";
import { ChurnLedger } from "./churn-ledger";
import { Gauges, type Gauge } from "./gauges";
import { LanguageSplit } from "./language-split";
import { ageLabel, gapLabel, momentum, shortCount } from "./numbers";
import { OriginBlock } from "./origin-block";
import { PunchCard } from "./punch-card";
import { RecordsList, type RecordLinks } from "./records-list";
import { DEFAULT_WEEKEND_PRESET } from "./weekend";

export interface StatsReportProps extends RecordLinks {
  data: StatsHighlights;
  /** Weekday indices (0 = Monday) the reader counts as the weekend. */
  weekendDays?: readonly number[];
  contributorsHref?: string | undefined;
}

const MICRO = "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]";
const H2 = "text-lg font-semibold text-[var(--color-text-primary)]";

/** "Mar 2026", in the viewer's zone; a date-only value stays on its own day. */
function monthYear(iso: string): string {
  return formatDateWith(iso, { month: "short", year: "numeric" });
}

function span(start: string, end: string): string {
  return `${formatDate(start)} to ${formatDate(end)}`;
}

/** What history the rhythm was drawn from, said beside the chart it qualifies. */
function scopeSentence(data: StatsHighlights): string | undefined {
  const w = data.rhythm.window;
  if (!w?.first_at || !w.last_at) return undefined;
  const range = `${monthYear(w.first_at)} to ${monthYear(w.last_at)}`;
  return w.complete
    ? `Every commit, ${range}.`
    : `Drawn from the latest ${formatNumber(w.commits)} of ${formatNumber(
        data.origin.total_commits,
      )} commits, ${range}.`;
}

/**
 * The one sentence a reader takes away: the repo named by the shape of its
 * work, with the evidence that earned the name right under it. When there is
 * too little history to name a rhythm, the size class leads instead.
 */
function Headline({ data, weekendDays }: { data: StatsHighlights; weekendDays: readonly number[] }) {
  const name = data.repo.name || "This repo";
  const archetype = repoArchetype(data.rhythm.punch_card?.matrix, weekendDays);
  const { size_class: size } = data.scale;
  const asOf = data.origin.last_commit_at;
  return (
    <header className="flex flex-col gap-3 pb-8">
      {asOf && <span className={MICRO}>As of {formatDate(asOf)}</span>}
      <p className="max-w-[24ch] text-[clamp(28px,6.5vw,44px)] font-normal leading-[1.1] tracking-[-0.02em] text-[var(--color-text-primary)] [text-wrap:balance]">
        {archetype ? (
          <>
            {name} ships like a <strong className="font-semibold">{archetype.name}</strong>.
          </>
        ) : (
          <>
            {name} is a <strong className="font-semibold">{size.name}</strong>.
          </>
        )}
      </p>
      <p className="max-w-[60ch] text-[18px] leading-snug text-[var(--color-text-secondary)] [text-wrap:pretty]">
        {archetype ? archetype.because : size.blurb}
      </p>
      <OriginBlock data={data.origin} />
      {data.index_storage_bytes ? (
        <p className="-mt-2 text-xs text-[var(--color-text-tertiary)]">
          Its Repowise index (documentation, symbols and vectors) takes{" "}
          {formatBytes(data.index_storage_bytes)} on disk.
        </p>
      ) : null}
    </header>
  );
}

/** The two "per 100" readouts: test lines against source, and lines taken back. */
function ratioGauges({ scale, churn }: StatsHighlights): Gauge[] {
  const tests = scale.test_nloc ?? 0;
  const source = scale.total_nloc - tests;
  const out: Gauge[] = [];
  if (tests > 0 && source > 0) {
    out.push({ label: "Tests", value: String(Math.round((tests / source) * 100)), unit: "per 100", sub: "lines of source" });
  }
  if (churn && churn.lines_added > 0) {
    out.push({
      label: "Taken back",
      value: String(Math.round(churn.deleted_per_hundred)),
      unit: "per 100",
      sub: "lines ever written",
    });
  }
  return out;
}

function leadGauges(data: StatsHighlights, contributorsHref: string | undefined): Gauge[] {
  const { scale, origin, rhythm } = data;
  const w = rhythm.window;
  const nloc = shortCount(scale.total_nloc);
  const exact = formatNumber(scale.total_nloc);
  const out: Gauge[] = [
    { label: "Lines of code", value: nloc, exact: nloc === exact ? undefined : exact, sub: scale.size_class.name },
  ];
  if (origin.age_days != null) {
    const since = origin.first_commit_at ? `since ${monthYear(origin.first_commit_at)}` : undefined;
    out.push({ label: "Age", ...ageLabel(origin.age_days), sub: since });
  }
  out.push(
    { label: "People", value: formatNumber(origin.contributor_count), sub: "have committed", href: contributorsHref },
    {
      label: "Active days",
      value: formatNumber(rhythm.active_days),
      sub: w && !w.complete ? `in the latest ${formatNumber(w.commits)} commits` : "with at least one commit",
    },
    ...ratioGauges(data),
  );
  return out;
}

const markInk = "bg-[var(--color-text-primary)]";

const days = (n: number) => (n === 1 ? "day" : "days");

function streakGauge({ longest_streak: s }: StatsHighlights["rhythm"]): Gauge | null {
  if (!s) return null;
  const ticks = Array.from({ length: Math.min(s.days, 60) }, (_, i) => (
    <span key={i} className={`max-w-[3px] flex-1 ${markInk} opacity-50`} />
  ));
  return {
    label: "Longest streak",
    value: formatNumber(s.days),
    unit: days(s.days),
    mark: <div className="flex h-3 gap-px overflow-hidden">{ticks}</div>,
    sub: span(s.start, s.end),
  };
}

function silenceGauge({ longest_silence: s }: StatsHighlights["rhythm"]): Gauge | null {
  if (!s) return null;
  return {
    label: "Longest silence",
    ...gapLabel(s.hours),
    mark: (
      <div className="relative h-3 border-x border-[var(--color-text-tertiary)]">
        <span className="absolute inset-x-0 top-1/2 border-t border-dashed border-[var(--color-text-tertiary)]" />
      </div>
    ),
    sub: span(s.start, s.end),
  };
}

function busiestDayGauge({ busiest_day: day, busiest_month: month }: StatsHighlights["rhythm"]): Gauge | null {
  if (!day) return null;
  const monthNote = month
    ? `. Busiest month ${monthYear(`${month.month}-01`)}, ${formatNumber(month.total)}`
    : "";
  return {
    label: "Busiest day",
    value: formatNumber(day.commits),
    unit: "commits",
    sub: `${formatDate(day.date)}${monthNote}`,
  };
}

function halfLifeGauge({ code_half_life_days: d }: StatsHighlights["rhythm"]): Gauge | null {
  if (d == null) return null;
  return {
    label: "Code half-life",
    value: formatNumber(d),
    unit: days(d),
    mark: (
      <div className="flex h-1.5">
        <span className={`flex-1 ${markInk} opacity-35`} />
        <span className={`flex-1 ${markInk} opacity-10`} />
      </div>
    ),
    sub: "Half the files have gone unchanged this long",
  };
}

/** The 90 days before against the last 90, drawn only when the comparison holds. */
function PaceBars({ v }: { v: StatsVelocity }) {
  const peak = Math.max(v.recent_90d, v.prior_90d, 1);
  const bars: Array<[string, number, string]> = [
    ["before", v.prior_90d, "opacity-35"],
    ["last 90", v.recent_90d, "opacity-80"],
  ];
  return (
    <div className="grid gap-1 font-mono text-[10px] text-[var(--color-text-tertiary)]">
      {bars.map(([label, n, op]) => (
        <div key={label} className="grid grid-cols-[42px_minmax(0,1fr)] items-center gap-1.5">
          {label}
          <span className={`h-1.5 ${markInk} ${op}`} style={{ width: `${Math.round((n / peak) * 100)}%` }} />
        </div>
      ))}
    </div>
  );
}

function paceGauge({ velocity: v }: StatsHighlights["rhythm"]): Gauge | null {
  const m = momentum(v);
  if (!v || !m) return null;
  return {
    label: "Last 90 days",
    value: m.value,
    unit: m.unit,
    mark: v.pct_change != null ? <PaceBars v={v} /> : undefined,
    sub: m.sub,
  };
}

function tempoGauges(data: StatsHighlights): Gauge[] {
  return [streakGauge, silenceGauge, busiestDayGauge, halfLifeGauge, paceGauge]
    .map((g) => g(data.rhythm))
    .filter((g): g is Gauge => g !== null);
}

/**
 * The Stats page as an instrument panel: one headline sentence, a ribbon of
 * readouts, then a hairline grid of the rhythm, tempo, records, ledger and
 * people. Ownership figures (truck factor, single-owner files, silos) are left
 * to Contributors, and file, symbol and commit totals to Overview and Commits.
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
  const { churn, rhythm, people, records } = data;
  const sample = rhythm.window;
  const partial = sample != null && !sample.complete;
  const tempo = tempoGauges(data);
  const languages = data.scale.languages ?? [];
  const block = "border-b border-[var(--color-border-default)] py-7";

  return (
    <div className="flex min-w-0 flex-col">
      <Headline data={data} weekendDays={weekendDays} />
      <Gauges
        gauges={leadGauges(data, contributorsHref)}
        LinkComponent={links.LinkComponent}
        className="border-y border-t-[var(--color-text-primary)] border-b-[var(--color-border-default)]"
      />

      <div className={block}>
        <PunchCard
          data={rhythm.punch_card}
          weekendDays={weekendDays}
          scope={scopeSentence(data)}
          partialCommits={partial ? sample.commits : undefined}
        />
      </div>

      {tempo.length > 0 && (
        <section aria-label="Tempo" className={`${block} flex flex-col gap-2`}>
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <h2 className={H2}>Tempo</h2>
            {partial && <span className={MICRO}>Latest {formatNumber(sample.commits)} commits</span>}
          </div>
          <Gauges gauges={tempo} />
        </section>
      )}

      <div className={`${block} grid grid-cols-1 gap-y-8 lg:grid-cols-12`}>
        <div className="min-w-0 lg:col-span-7 lg:pr-6">
          <RecordsList
            records={records}
            commitScope={partial ? `latest ${formatNumber(sample.commits)}` : undefined}
            {...links}
          />
        </div>
        <aside className="flex min-w-0 flex-col gap-8 border-t border-[var(--color-border-default)] pt-8 lg:col-span-5 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
          {churn && <ChurnLedger data={churn} />}
          {languages.length > 0 && <LanguageSplit languages={languages} />}
          <ArrivalsTimeline arrivals={people.arrivals} partialSince={partial ? sample.first_at : null} />
        </aside>
      </div>

      {people.chronotypes.length > 0 ? (
        <div className="py-7">
          <ChronotypeList people={people.chronotypes} weekendDays={weekendDays} />
        </div>
      ) : (
        rhythm.punch_card.timezone_mode === "utc" && (
          <p className="py-7 text-xs text-[var(--color-text-secondary)]">
            Commit-hour habits need each commit&apos;s local time, which this index does not carry.
          </p>
        )
      )}
    </div>
  );
}
