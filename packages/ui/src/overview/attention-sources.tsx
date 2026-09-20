import * as React from "react";
import { attentionSourceHref, attentionSourceLabel } from "../dashboard/attention-href";
import { formatNumber } from "../lib/format";

/**
 * What the attention list is made of, beside the rows it could fit.
 *
 * The rows are ranked by severity, which is right and has a consequence: on a
 * repository whose worst findings all come from one detector, six rows can be
 * six code-health findings, and a section titled "Needs attention" then looks
 * like it only knows about one thing. The breakdown is the honest fix. It says
 * what the other sources hold without spending a row on a finding that is
 * genuinely less urgent than the ones above it, and it gives every source a
 * way in even when none of its items made the cut.
 *
 * Counts, not proportions. A bar would imply the sources are commensurable,
 * and 15,892 health findings against 245 security findings is not a ratio
 * anybody should read as one.
 */
export function AttentionSources({
  bySource,
  prefix,
  LinkComponent,
}: {
  /** `attention_summary.by_source` — keyed by item type, plus `decisions`. */
  bySource: Record<string, number>;
  prefix: string;
  LinkComponent?: React.ElementType | undefined;
}) {
  const A = LinkComponent ?? "a";
  const entries = Object.entries(bySource)
    .filter(([, count]) => count > 0)
    .sort(([, a], [, b]) => b - a);
  if (entries.length === 0) return null;

  return (
    <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1.5 pt-1">
      {entries.map(([source, count]) => (
        <A
          key={source}
          href={attentionSourceHref(source, prefix)}
          className="group text-[11px] text-[var(--color-text-tertiary)] no-underline"
        >
          <span className="font-mono font-semibold tabular-nums text-[var(--color-text-secondary)] transition-colors group-hover:text-[var(--color-accent-primary)]">
            {formatNumber(count)}
          </span>{" "}
          <span className="group-hover:underline">{attentionSourceLabel(source)}</span>
        </A>
      ))}
    </div>
  );
}
