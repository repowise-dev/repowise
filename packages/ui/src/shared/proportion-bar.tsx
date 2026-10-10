import * as React from "react";
import { cn } from "../lib/cn";

/**
 * The share steps, largest share first. Literal strings so Tailwind's scan
 * keeps the tokens; see `--color-share-*` in globals.css.
 */
export const SHARE_STEPS = [
  "var(--color-share-1)",
  "var(--color-share-2)",
  "var(--color-share-3)",
  "var(--color-share-4)",
  "var(--color-share-5)",
] as const;

/** The collapsed remainder: present, but not a step. */
export const SHARE_TAIL = "var(--color-share-tail)";

/** Fill for the segment at `rank` (0 = largest). Past the last step, the tail. */
export function shareColor(rank: number): string {
  return SHARE_STEPS[rank] ?? SHARE_TAIL;
}

export interface ProportionSegment {
  key: string;
  label: string;
  value: number;
  /** Legend figure. Defaults to the rounded share ("42%"). */
  detail?: string | undefined;
  /**
   * Semantic fill, only for segments that are a verdict (health band,
   * severity, freshness). Everything else takes the share steps.
   */
  color?: string | undefined;
  /** An unitemised remainder ("others"): drawn last, in the tail step. */
  tail?: boolean | undefined;
  href?: string | undefined;
  onSelect?: (() => void) | undefined;
}

export interface ProportionBarProps {
  segments: ProportionSegment[];
  /** What the bar splits, for assistive tech: "Owned files by contributor". */
  label: string;
  /**
   * Largest first (default). Turn off for an ordered scale (health bands,
   * severity, confidence) whose order is the point; steps then follow it.
   */
  sort?: boolean;
  /** Named segments before the rest fold into one tail. Default 5, one per step. */
  maxSegments?: number;
  /** Label for the folded tail. */
  othersLabel?: (count: number) => string;
  /** Default true. Off where the figure beside the bar already names the split. */
  legend?: boolean;
  /** `sm` for a bar inside a line of text, `md` (default) standing alone. */
  size?: "sm" | "md";
  LinkComponent?: React.ElementType | undefined;
  className?: string | undefined;
}

interface Row {
  key: string;
  label: string;
  value: number;
  detail: string;
  fill: string;
  href?: string | undefined;
  onSelect?: (() => void) | undefined;
}

function pctText(value: number, total: number): string {
  const pct = (value / total) * 100;
  return pct > 0 && pct < 1 ? "<1%" : `${Math.round(pct)}%`;
}

function buildRows(
  segments: ProportionSegment[],
  sort: boolean,
  maxSegments: number,
  othersLabel: (count: number) => string,
): { rows: Row[]; total: number } {
  const live = segments.filter((s) => Number.isFinite(s.value) && s.value > 0);
  const named = live.filter((s) => !s.tail);
  const tails = live.filter((s) => s.tail);
  if (sort) named.sort((a, b) => b.value - a.value);

  const shown = named.slice(0, maxSegments);
  const folded = named.slice(maxSegments);
  const tailSegments: ProportionSegment[] = [...tails];
  if (folded.length > 0) {
    tailSegments.push({
      key: "__others",
      label: othersLabel(folded.length),
      value: folded.reduce((s, x) => s + x.value, 0),
    });
  }

  const total = live.reduce((s, x) => s + x.value, 0);
  const toRow = (s: ProportionSegment, fill: string): Row => ({
    key: s.key,
    label: s.label,
    value: s.value,
    detail: s.detail ?? pctText(s.value, total),
    fill,
    href: s.href,
    onSelect: s.onSelect,
  });
  return {
    rows: [
      ...shown.map((s, i) => toRow(s, s.color ?? shareColor(i))),
      ...tailSegments.map((s) => toRow(s, s.color ?? SHARE_TAIL)),
    ],
    total,
  };
}

/**
 * A share of a whole, as one bar and a legend.
 *
 * Every proportion in the product goes through this, so they all read the
 * same: ink steps from strongest to faintest, largest share first, hairline
 * gaps between segments, the long tail folded into "N others". Hue is kept
 * for bars whose segments are a verdict, which pass `color`.
 *
 * With the legend on, the legend is the accessible reading (a labelled list)
 * and the bar is decoration; with it off, the bar carries the summary itself.
 */
export function ProportionBar({
  segments,
  label,
  sort = true,
  maxSegments = SHARE_STEPS.length,
  othersLabel = (n) => `${n.toLocaleString()} others`,
  legend = true,
  size = "md",
  LinkComponent,
  className,
}: ProportionBarProps) {
  const { rows, total } = buildRows(segments, sort, maxSegments, othersLabel);
  if (total === 0) return null;

  const summary = `${label}: ${rows.map((r) => `${r.label} ${r.detail}`).join(", ")}`;

  return (
    <div className={cn("flex min-w-0 flex-col gap-2", className)}>
      <div
        {...(legend ? { "aria-hidden": true } : { role: "img", "aria-label": summary })}
        className={cn(
          "flex w-full gap-px overflow-hidden rounded-full",
          size === "sm" ? "h-1.5" : "h-2.5",
        )}
      >
        {rows.map((r) => (
          <span
            key={r.key}
            title={`${r.label}: ${r.detail}`}
            className="h-full min-w-[2px]"
            style={{ flex: `${r.value} 1 0%`, background: r.fill }}
          />
        ))}
      </div>
      {legend && <ProportionLegend rows={rows} label={label} LinkComponent={LinkComponent} />}
    </div>
  );
}

function ProportionLegend({
  rows,
  label,
  LinkComponent,
}: {
  rows: Row[];
  label: string;
  LinkComponent?: React.ElementType | undefined;
}) {
  const A = LinkComponent ?? "a";
  return (
    <ul aria-label={label} className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
      {rows.map((r) => {
        const body = (
          <>
            <span
              aria-hidden
              className="h-2 w-2 shrink-0 rounded-[2px]"
              style={{ background: r.fill }}
            />
            <span className="min-w-0 text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
              {r.label}
            </span>
            <span className="shrink-0 tabular-nums text-[var(--color-text-tertiary)]">
              {r.detail}
            </span>
          </>
        );
        const item = "flex min-w-0 items-center gap-1.5";
        // Interactive entries keep a 32px target on touch; desktop rows stay tight.
        const hit = `${item} min-h-8 text-left no-underline sm:min-h-0 [&:hover>span:nth-child(2)]:text-[var(--color-text-primary)]`;
        return (
          <li key={r.key} className="min-w-0">
            {r.href ? (
              <A href={r.href} className={hit}>
                {body}
              </A>
            ) : r.onSelect ? (
              <button type="button" onClick={r.onSelect} className={hit}>
                {body}
              </button>
            ) : (
              <span className={item}>{body}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
