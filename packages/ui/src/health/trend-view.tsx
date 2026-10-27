"use client";

/**
 * Trend view — the KPI history over recent indexes, on the section design
 * language.
 *
 * It used to open with three `MetricCard`s in a grid, then stack tinted alert
 * boxes and two dashed empty-state boxes under them: six bordered containers at
 * near-identical weight for what is really two numbers, a warning and a chart.
 * The figures are a hairline `StatRibbon` now, the alerts are rows that spend
 * their colour on the icon and the label rather than on a ground, and the empty
 * states are sentences saying what will fill them.
 *
 * The single-snapshot case stays explicit. A trend line drawn through one point
 * is a flat line, which reads as "stable" when the truth is "not measured yet".
 *
 * Trend data is fetched once by the host and passed in, so it is not
 * double-fetched alongside anything else on the page.
 */

import { useMemo, useState } from "react";
import { AlertTriangle, Info } from "lucide-react";
import type { HealthCounts, HealthTrendResponse } from "@repowise-dev/types/health";
import { formatScore } from "@repowise-dev/types/health";

import { Skeleton, SkeletonRegion } from "../ui/skeleton";
import { ApiError } from "../shared/api-error";
import { EmptyState } from "../shared/empty-state";
import { toFriendlyMessage } from "../lib/errors";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";

import { TrendChart } from "./trend-chart";
import { deltaColor, formatDelta, scoreTextColor } from "./tokens";

/** Rows each Movers group lists before "Show more". */
const GROUP_MAX = 8;

/**
 * Colour, lead phrase and icon per alert kind.
 *
 * A `history_drag` fall is not the reader's doing and there is nothing to act
 * on, so it takes the neutral treatment the drawer gives history findings
 * rather than the error red. Painting it red is what told a reader their
 * refactoring had made things worse. An unknown kind falls through to the
 * warning treatment, which is the safe reading of a signal we cannot classify.
 */
function alertTreatment(kind: string): { color: string; label: string; watch: boolean } {
  if (kind === "declining") {
    // Not "Declining health.": three different figures raise this alert and the
    // message names which one, so a lead that named a fourth thing contradicted it.
    return { color: "var(--color-error)", label: "Declining.", watch: false };
  }
  if (kind === "history_drag") {
    return {
      color: "var(--color-text-secondary)",
      label: "Change history, not code.",
      watch: true,
    };
  }
  return { color: "var(--color-warning)", label: "Predicted decline.", watch: false };
}

type TrendSummary = HealthTrendResponse["summary"];

// "0.00 vs. 7.5" is the shape a delta takes when nothing happened, and it
// reads as a measurement rather than as "no news". Say it plainly instead.
function deltaSub(delta: number | null, previous: number | null) {
  if (delta == null) return "no prior snapshot";
  if (Math.abs(delta) < 0.05) return "unchanged since last index";
  return `${formatDelta(delta)} vs. ${previous?.toFixed(1) ?? "—"}`;
}

function codeHealthStat(summary: TrendSummary, otherReading: boolean): RibbonStat {
  return {
    label: "Code health",
    value: otherReading ? "—" : formatScore(summary.current_average_health),
    ...(otherReading
      ? { sub: "recorded on the full score" }
      : {
          valueColor: scoreTextColor(summary.current_average_health),
          sub: deltaSub(summary.average_delta, summary.previous_average_health),
          ...(Math.abs(summary.average_delta ?? 0) >= 0.05
            ? { subColor: deltaColor(summary.average_delta) }
            : {}),
        }),
  };
}

function hotspotStat(
  summary: TrendSummary,
  otherReading: boolean,
  narrowed: boolean,
): RibbonStat {
  // Absent for one of two reasons. Under a narrowed scope only the average was
  // recorded for both populations, so a repo-wide hotspot figure would describe
  // files this view has dropped. On the full scope it means the newest snapshot
  // recorded no hotspot files at all. Say which, rather than blaming the scope
  // for a repository that simply has none.
  const hotspot = summary.current_hotspot_health;
  return {
    label: "Hotspot health",
    value: otherReading || hotspot == null ? "—" : formatScore(hotspot),
    ...(otherReading
      ? { sub: "recorded on the full score" }
      : {
          ...(hotspot == null ? {} : { valueColor: scoreTextColor(hotspot) }),
          sub:
            hotspot == null
              ? narrowed
                ? "not measured for this scope"
                : "no hotspot files"
              : deltaSub(summary.hotspot_delta, summary.previous_hotspot_health),
          ...(hotspot != null && Math.abs(summary.hotspot_delta ?? 0) >= 0.05
            ? { subColor: deltaColor(summary.hotspot_delta) }
            : {}),
        }),
  };
}

function TrendAlerts({ alerts }: { alerts: HealthTrendResponse["alerts"] }) {
  if (alerts.length === 0) return null;
  return (
    <div className="flex flex-col gap-2">
      {alerts.map((a, i) => {
        const { color, label, watch } = alertTreatment(a.kind);
        const Icon = watch ? Info : AlertTriangle;
        return (
          <p key={i} className="flex items-start gap-2 text-sm">
            <Icon className="mt-0.5 h-4 w-4 shrink-0" style={{ color }} aria-hidden />
            <span>
              <strong className="font-semibold" style={{ color }}>
                {label}
              </strong>{" "}
              <span className="text-[var(--color-text-secondary)]">{a.message}</span>
            </span>
          </p>
        );
      })}
    </div>
  );
}

type FileDelta = HealthTrendResponse["file_deltas"][number];

/** A path as the name a reader scans for and the folder that places it. */
function splitPath(path: string): { name: string; dir: string } {
  const at = path.lastIndexOf("/");
  return at < 0 ? { name: path, dir: "" } : { name: path.slice(at + 1), dir: path.slice(0, at) };
}

/**
 * The line above the groups. The server's per-direction counts describe every
 * file that moved; without them (the hosted backend sends none) the counts are
 * of the rows that arrived, and the sentence says so.
 */
function moversSummary(
  data: HealthTrendResponse,
  worse: number,
  improved: number,
  net: number | null,
): string {
  const exact = data.file_deltas_worse != null && data.file_deltas_improved != null;
  const counts = exact
    ? `${data.file_deltas_worse} got worse · ${data.file_deltas_improved} improved`
    : `Among the ${data.file_deltas.length} largest changes: ${worse} got worse · ${improved} improved`;
  return net == null || Math.abs(net) < 0.005 ? counts : `${counts} · code health ${formatDelta(net)}`;
}

function MoverRow({
  row,
  scale,
  onSelectFile,
}: {
  row: FileDelta;
  scale: number;
  onSelectFile?: ((path: string) => void) | undefined;
}) {
  const { name, dir } = splitPath(row.file_path);
  const body = (
    <>
      <span className="flex min-w-0 items-baseline gap-2">
        <span className="shrink-0 font-mono text-xs text-[var(--color-text-primary)]">{name}</span>
        {dir && (
          <span className="min-w-0 truncate font-mono text-xs text-[var(--color-text-tertiary)]" title={dir}>
            {dir}
          </span>
        )}
      </span>
      <span className="font-mono text-xs tabular-nums text-[var(--color-text-secondary)]">
        {formatScore(row.before)} → {formatScore(row.after)}
      </span>
      {/* Length is the size of the move against the largest one listed. */}
      <span className="hidden h-1.5 w-16 overflow-hidden rounded-full bg-[var(--color-bg-inset)] sm:block" aria-hidden>
        <span
          className={`block h-full rounded-full ${row.delta < 0 ? "bg-[var(--color-error)]" : "bg-[var(--color-success)]"}`}
          style={{ width: `${Math.max(6, (Math.abs(row.delta) / scale) * 100)}%` }}
        />
      </span>
      <span className={`w-12 text-right font-mono text-xs tabular-nums ${deltaColor(row.delta)}`}>
        {formatDelta(row.delta)}
      </span>
    </>
  );
  const grid =
    "grid w-full grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-3 px-2 py-1.5 text-left sm:grid-cols-[minmax(0,1fr)_auto_auto_auto]";
  return (
    <li>
      {onSelectFile ? (
        <button
          type="button"
          onClick={() => onSelectFile(row.file_path)}
          title={row.file_path}
          className={`${grid} rounded transition-colors hover:bg-[var(--color-bg-elevated)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-focus-ring)]`}
        >
          {body}
        </button>
      ) : (
        <div className={grid}>{body}</div>
      )}
    </li>
  );
}

function MoverGroup({
  title,
  rows,
  total,
  scale,
  onSelectFile,
}: {
  title: string;
  rows: FileDelta[];
  /** Every file that moved this way, when the server counted them. */
  total: number | undefined;
  scale: number;
  onSelectFile?: ((path: string) => void) | undefined;
}) {
  const [open, setOpen] = useState(false);
  if (rows.length === 0) return null;
  const listed = open ? rows : rows.slice(0, GROUP_MAX);
  const of = total ?? rows.length;
  return (
    <div className="flex flex-col gap-1">
      <p className="flex items-baseline gap-2 px-2 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        <span>{title}</span>
        <span className="tabular-nums">{listed.length < of ? `${listed.length} of ${of}` : of}</span>
      </p>
      <ul className="flex flex-col">
        {listed.map((r) => (
          <MoverRow key={r.file_path} row={r} scale={scale} onSelectFile={onSelectFile} />
        ))}
      </ul>
      {rows.length > GROUP_MAX && (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="self-start rounded px-2 py-1 text-xs text-[var(--color-accent-primary)] hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-focus-ring)]"
        >
          {open ? "Show fewer" : `Show ${rows.length - GROUP_MAX} more`}
        </button>
      )}
    </div>
  );
}

/**
 * The files that moved between the last two snapshots, as two ranked groups:
 * what got worse first, since that is the work, then what improved. Each row
 * names the file, places it by folder, and opens it when the host can.
 *
 * Ceiling: no cause per row. Snapshots keep one score per file, not the
 * findings behind it, so why a file moved is not stored. Upgrade path: record
 * each file's lead finding on the snapshot and compare the two.
 */
function MoversSection({
  data,
  singleSnapshot,
  net,
  onSelectFile,
}: {
  data: HealthTrendResponse;
  singleSnapshot: boolean;
  net: number | null;
  onSelectFile?: ((path: string) => void) | undefined;
}) {
  const worse = data.file_deltas.filter((d) => d.delta < 0).sort((a, b) => a.delta - b.delta);
  const improved = data.file_deltas.filter((d) => d.delta > 0).sort((a, b) => b.delta - a.delta);
  const scale = Math.max(0.01, ...data.file_deltas.map((d) => Math.abs(d.delta)));
  return (
    <section className="flex flex-col gap-3">
      <h3 className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        Movers since last index
      </h3>
      {data.file_deltas.length === 0 ? (
        <p className="max-w-[62ch] text-[15px] text-[var(--color-text-secondary)]">
          {singleSnapshot
            ? "Per-file movement compares the last two snapshots, so it appears with the second one."
            : "No file changed score between the last two snapshots."}
        </p>
      ) : (
        <>
          <p className="text-[15px] tabular-nums text-[var(--color-text-secondary)]">
            {moversSummary(data, worse.length, improved.length, net)}
          </p>
          <div className="flex flex-col gap-4">
            <MoverGroup
              title="Got worse"
              rows={worse}
              total={data.file_deltas_worse}
              scale={scale}
              onSelectFile={onSelectFile}
            />
            <MoverGroup
              title="Improved"
              rows={improved}
              total={data.file_deltas_improved}
              scale={scale}
              onSelectFile={onSelectFile}
            />
          </div>
        </>
      )}
    </section>
  );
}

function trendStats(data: HealthTrendResponse, otherReading: boolean): RibbonStat[] {
  // The response names the scope it used, which is what tells a null hotspot
  // figure under a narrowed scope apart from one on the full scope.
  const narrowed = data.scope === "production";
  return [
    codeHealthStat(data.summary, otherReading),
    hotspotStat(data.summary, otherReading, narrowed),
    { label: "Snapshots", value: String(data.snapshot_count), sub: "rolling window, 50 max" },
  ];
}

function TrendBody({
  data,
  counts,
  onSelectFile,
}: {
  data: HealthTrendResponse;
  counts?: HealthCounts | undefined;
  onSelectFile?: ((path: string) => void) | undefined;
}) {
  const singleSnapshot = data.snapshot_count <= 1;
  // Snapshots recorded the full score, so there is no code-shape series to
  // read. These two carry the same labels as the lede's figures, and showing
  // the other reading of them here put two different numbers under one name on
  // one screen — the exact confusion the single headline exists to end.
  const otherReading = counts === "code_shape";
  // Oldest first, and stable across renders so the chart's geometry is not
  // rebuilt by a parent re-render that did not change the data.
  const history = useMemo(() => [...data.history].reverse(), [data.history]);

  return (
    <div className="flex flex-col gap-6">
      <StatRibbon stats={trendStats(data, otherReading)} />

      <TrendAlerts alerts={data.alerts} />

      {singleSnapshot ? (
        <p className="max-w-[62ch] text-sm text-[var(--color-text-secondary)]">
          One snapshot so far. The trend lines appear once a second one lands, after the next
          index update.
        </p>
      ) : (
        <TrendChart history={history} />
      )}

      <MoversSection
        data={data}
        singleSnapshot={singleSnapshot}
        // The code-health delta reads the full score, which the other reading
        // does not show, so it stays off the line there.
        net={otherReading ? null : data.summary.average_delta}
        onSelectFile={onSelectFile}
      />
    </div>
  );
}

export function TrendView({
  data,
  isLoading,
  error,
  onRetry,
  counts,
  onSelectFile,
}: {
  data: HealthTrendResponse | undefined;
  isLoading: boolean;
  error: unknown;
  onRetry?: (() => void) | undefined;
  /** What the page's figures count, so this section cannot claim the other reading. */
  counts?: HealthCounts;
  /** Opens a file, from a Movers row. Rows are plain text without it. */
  onSelectFile?: ((path: string) => void) | undefined;
}) {
  if (isLoading) {
    return (
      <SkeletonRegion className="flex flex-col gap-6" label="Loading trend">
        <Skeleton className="h-[72px] w-full" />
        <Skeleton className="h-[220px] w-full" />
      </SkeletonRegion>
    );
  }
  if (error) {
    return (
      <ApiError
        size="compact"
        title="Couldn't load trend data"
        message={toFriendlyMessage(error)}
        {...(onRetry ? { onRetry } : {})}
      />
    );
  }
  if (!data) {
    return (
      <EmptyState
        size="compact"
        title="No trend recorded yet"
        description="Each index update records a snapshot, and the trend appears from the second one."
      />
    );
  }
  return <TrendBody data={data} counts={counts} onSelectFile={onSelectFile} />;
}
