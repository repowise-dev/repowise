import type { StatsChurn } from "@repowise-dev/types/stats";
import { formatNumber } from "../lib/format";

/**
 * Lifetime lines written, and how many were later taken back.
 *
 * One bar, three readings. The full width is everything ever written; the
 * hatched tail is what was deleted; what remains is what survived. Deletion
 * reads as erosion of the whole rather than as a second competing bar, which is
 * what a diverging two-sided chart would imply — and "taken back" is a share of
 * the work, not its opposite.
 *
 * The ratio line underneath is the point of the whole component.
 */
export function ChurnLedger({ data }: { data: StatsChurn }) {
  if (!data || data.lines_added <= 0) return null;

  // Guard the pathological case where a rewrite deletes more than the running
  // total ever added: clamp so the bar can't invert or overflow its track.
  const deleted = Math.max(0, Math.min(data.lines_deleted, data.lines_added));
  const standing = data.lines_added - deleted;
  const standingPct = (standing / data.lines_added) * 100;

  const figures = [
    { label: "Written", value: data.lines_added, tone: "var(--color-text-primary)" },
    { label: "Taken back", value: data.lines_deleted, tone: "var(--color-error)" },
    // `standing`, not the server's `net`: a repo that has deleted more than it
    // ever added (a big vendored-tree removal) drives net negative, which would
    // read as "-40,000 still standing" beside a bar showing zero.
    { label: "Still standing", value: standing, tone: "var(--color-success)" },
  ];

  return (
    <section aria-label="Lifetime line churn" className="flex flex-col gap-3.5">
      <div
        className="flex h-11 overflow-hidden rounded-md border border-[var(--color-border-default)]"
        role="img"
        aria-label={`${formatNumber(data.lines_added)} lines written, of which ${formatNumber(
          data.lines_deleted,
        )} were later deleted`}
      >
        <div
          className="relative shrink-0 bg-[var(--color-success-muted,rgba(47,125,82,0.12))]"
          style={{ width: `${standingPct}%` }}
        >
          <span className="absolute inset-0 flex items-center truncate px-3 font-mono text-[11px] uppercase tracking-[0.06em] text-[var(--color-success)]">
            {formatNumber(standing)} standing
          </span>
        </div>
        <div
          className="relative flex-1 border-l border-[var(--color-border-default)]"
          style={{
            // Hatching rather than a flat fill: the deleted share is a texture
            // over the same track, not a separate quantity beside it.
            background:
              "repeating-linear-gradient(-45deg, var(--color-error-muted, rgba(163,58,50,0.14)) 0 6px, transparent 6px 12px)",
          }}
        >
          <span className="absolute inset-0 flex items-center truncate px-3 font-mono text-[11px] uppercase tracking-[0.06em] text-[var(--color-error)]">
            {formatNumber(deleted)} cut
          </span>
        </div>
      </div>

      <div className="flex flex-wrap gap-x-10 gap-y-3">
        {figures.map((f) => (
          <div key={f.label}>
            <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              {f.label}
            </p>
            <p
              className="text-2xl font-semibold tabular-nums leading-tight"
              style={{ color: f.tone }}
            >
              {formatNumber(f.value)}
            </p>
          </div>
        ))}
      </div>

      <p className="text-[15px] text-[var(--color-text-primary)]">
        For every 100 lines ever written here,{" "}
        <strong className="font-semibold text-[var(--color-accent-primary)]">
          {Math.round(data.deleted_per_hundred)} were taken back
        </strong>
        .
      </p>
    </section>
  );
}
