import type { CSSProperties } from "react";
import type { StatsChurn } from "@repowise-dev/types/stats";
import { formatNumber } from "../lib/format";
import { shortCount } from "./numbers";

/** A legend swatch followed by a shortened figure whose exact value is on hover. */
function Key({ swatch, n, label }: { swatch: CSSProperties; n: number; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5" title={`${formatNumber(n)} ${label}`}>
      <span className="inline-block h-2 w-2 shrink-0" style={swatch} aria-hidden />
      {shortCount(n)} {label}
    </span>
  );
}

const HATCH = "repeating-linear-gradient(-45deg, var(--color-border-hover) 0 4px, transparent 4px 8px)";

/**
 * Lifetime lines written, and how many were later taken back.
 *
 * The sentence is the point; the bar under it shows the same split. Its full
 * width is everything ever written and the hatched tail is what was deleted,
 * so deletion reads as erosion of the whole rather than a second bar.
 */
export function ChurnLedger({ data }: { data: StatsChurn }) {
  if (!data || data.lines_added <= 0) return null;

  // Clamp the pathological case where a rewrite deletes more than was ever
  // added, so the bar can't invert or overflow its track.
  const deleted = Math.max(0, Math.min(data.lines_deleted, data.lines_added));
  // `standing`, not the server's `net`: net can go negative after a big
  // vendored-tree removal, which would read as "-40,000 still standing".
  const standing = data.lines_added - deleted;

  return (
    <section aria-label="Lifetime line churn" className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">The ledger</h2>
      <p className="text-[22px] leading-snug text-[var(--color-text-primary)]">
        For every 100 lines ever written,{" "}
        <strong className="font-semibold">{Math.round(data.deleted_per_hundred)} were taken back.</strong>
      </p>
      <div
        className="flex h-2.5"
        role="img"
        aria-label={`${formatNumber(standing)} lines standing of ${formatNumber(
          data.lines_added,
        )} written; ${formatNumber(deleted)} were later deleted`}
      >
        <div
          className="h-full bg-[var(--color-text-primary)] opacity-80"
          style={{ width: `${(standing / data.lines_added) * 100}%` }}
        />
        <div className="h-full flex-1" style={{ background: HATCH }} />
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1.5 text-xs text-[var(--color-text-secondary)]">
        <Key swatch={{ background: "var(--color-text-primary)", opacity: 0.8 }} n={standing} label="standing" />
        <Key swatch={{ background: HATCH }} n={deleted} label="taken back" />
        <Key swatch={{ border: "1px solid var(--color-border-hover)" }} n={data.lines_added} label="written" />
      </div>
    </section>
  );
}
