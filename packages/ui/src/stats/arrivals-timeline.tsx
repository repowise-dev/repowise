import type { StatsArrival } from "@repowise-dev/types/stats";
import { formatDate, formatDateWith, formatNumber, parseDate } from "../lib/format";

const FIRST_SHOWN = 3;
const W = 600;
const H = 120;

/** "Mar 2026", in the viewer's zone like the arrival dates beside it. */
function monthYear(iso: string): string {
  return formatDateWith(iso, { month: "short", year: "numeric" });
}

/** Cumulative arrivals as a step line, in a W x H box stretched to its container. */
function curvePath(times: number[]): string {
  const t0 = times[0]!;
  const t1 = times[times.length - 1]!;
  const x = (t: number) => (((t - t0) / Math.max(1, t1 - t0)) * W).toFixed(1);
  const n = times.length;
  let d = `M0,${H}`;
  times.forEach((t, i) => {
    d += ` L${x(t)},${(H - (i / n) * H).toFixed(1)} L${x(t)},${(H - ((i + 1) / n) * H).toFixed(1)}`;
  });
  return `${d} L${W},0`;
}

/**
 * How the team grew: contributors over time as a cumulative curve, and the
 * first few names.
 *
 * The Contributors page shows one person's join date on their own profile, so
 * the sequence exists nowhere else. A curve shows the shape (when hiring sped
 * up) in one glance, which a 268-row roll never did.
 */
export function ArrivalsTimeline({
  arrivals,
  partialSince = null,
}: {
  arrivals: StatsArrival[];
  /** Start of the indexed commit window when it is not the whole history. */
  partialSince?: string | null | undefined;
}) {
  const dated = (arrivals ?? []).filter((a): a is StatsArrival & { first_commit_at: string } =>
    Boolean(a.first_commit_at),
  );
  if (dated.length === 0) return null;
  const times = dated.map((a) => parseDate(a.first_commit_at).getTime());
  const d = curvePath(times);
  const first = dated[0]!.first_commit_at;
  const last = dated[dated.length - 1]!.first_commit_at;

  return (
    <section aria-label="Contributor arrivals" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">
          {partialSince ? "First seen" : "Arrivals"}
        </h2>
        <span className="font-mono text-[10px] uppercase tabular-nums tracking-[0.12em] text-[var(--color-text-tertiary)]">
          {formatNumber(dated.length)} people
        </span>
      </div>
      {dated.length > 1 && (
        <div>
          <svg
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            className="block h-[100px] w-full"
            role="img"
            aria-label={`${formatNumber(dated.length)} contributors arrived between ${monthYear(first)} and ${monthYear(last)}`}
          >
            <path d={`${d} L${W},${H} Z`} fill="var(--color-text-primary)" opacity={0.06} />
            <path
              d={d}
              fill="none"
              stroke="var(--color-text-primary)"
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
            />
          </svg>
          <div className="mt-1.5 flex justify-between font-mono text-[10px] uppercase text-[var(--color-text-tertiary)]">
            <span>{monthYear(first)}</span>
            <span>{monthYear(last)}</span>
          </div>
        </div>
      )}
      <ol className="flex flex-col border-t border-[var(--color-border-default)]">
        {dated.slice(0, FIRST_SHOWN).map((a, i) => (
          <li
            key={`${a.name}-${a.first_commit_at}`}
            className="flex items-baseline justify-between gap-3 border-b border-[var(--color-border-default)] py-2"
          >
            <span className="min-w-0 break-words text-[15px] text-[var(--color-text-primary)]">
              {i + 1}. {a.name}
            </span>
            <span className="shrink-0 font-mono text-xs tabular-nums text-[var(--color-text-tertiary)]">
              {formatDate(a.first_commit_at)}
            </span>
          </li>
        ))}
      </ol>
      {partialSince && (
        <p className="text-xs text-[var(--color-text-tertiary)]">
          The index holds commits from {formatDate(partialSince)} on, so this is when each person
          first appears in that window. Anyone here may have arrived before it.
        </p>
      )}
    </section>
  );
}
