import * as React from "react";
import type { OverviewAttentionAreaRow } from "@repowise-dev/types/overview";
import {
  attentionSourceHref,
  attentionSourceLabel,
  type AttentionSeverity,
} from "../dashboard/attention-href";
import { biomarkerLabel } from "../health/biomarker-glossary";
import { formatNumber } from "../lib/format";

const SEVERITY_COLOR: Record<AttentionSeverity, string> = {
  // Critical and high share the one red. The palette has a single "this is
  // bad" hue and a second would spend the distinction on nothing: the band is
  // already named in the screen-reader text and carried by the row order.
  critical: "var(--color-error)",
  high: "var(--color-error)",
  medium: "var(--color-warning)",
  low: "var(--color-text-tertiary)",
};

/** What to call the lead item's kind. Health findings name their biomarker. */
function leadKind(lead: NonNullable<OverviewAttentionAreaRow["lead"]>): string {
  if (lead.type === "health_finding" && lead.subtype) return biomarkerLabel(lead.subtype);
  return "";
}

/**
 * Attention as one row per area of work.
 *
 * This replaces a list of individual findings, which does not survive real
 * volume. With roughly sixteen thousand open health findings against twenty
 * documentation-drift findings, six ranked rows are six health findings: a
 * random sample of the largest store, presented as if it were a worklist, with
 * every other area invisible. Nobody can act on "change entropy in report.py"
 * pulled out of a pile that size.
 *
 * A row is deliberately both things at once. The count says how much the area
 * holds, the lead names the single worst thing in it so the row is concrete
 * rather than a category, and the whole row links to the page that owns the
 * subject. That is what keeps this from being the sidebar with numbers glued
 * on: a row you can only navigate from belongs in navigation, a row that
 * reports state belongs here.
 *
 * Fixed order, never re-sorted by severity. These are stable subjects, and a
 * list whose rows move between visits cannot be scanned by position — you have
 * to re-read it every time. Severity is carried by the dot and by the lead.
 */
export function AttentionAreas({
  areas,
  prefix,
  LinkComponent,
}: {
  areas: OverviewAttentionAreaRow[];
  prefix: string;
  LinkComponent?: React.ElementType | undefined;
}) {
  const A = LinkComponent ?? "a";
  if (areas.length === 0) {
    return (
      <p className="text-xs text-[var(--color-success)]">Nothing needs attention right now.</p>
    );
  }

  return (
    <ul className="m-0 list-none divide-y divide-[var(--color-border-default)] border-t border-[var(--color-border-default)] p-0">
      {areas.map((area) => {
        const kind = area.lead ? leadKind(area.lead) : "";
        return (
          <li key={area.key}>
            <A
              href={attentionSourceHref(area.key, prefix)}
              className="group flex flex-col gap-1 py-3 text-xs no-underline sm:flex-row sm:items-baseline sm:gap-4"
            >
              <span className="flex shrink-0 items-center gap-2 sm:w-40">
                <span
                  aria-hidden
                  className="h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ background: SEVERITY_COLOR[area.severity] }}
                />
                {/* The dot is nothing at all to a screen reader, and to anyone
                    who cannot separate the hues. The text equivalent goes here
                    rather than into a `title`, which assistive tech reads
                    inconsistently. */}
                <span className="sr-only">worst item is {area.severity} severity. </span>
                <span className="font-medium text-[var(--color-text-primary)] group-hover:text-[var(--color-accent-primary)]">
                  {attentionSourceLabel(area.key)}
                </span>
              </span>

              <span className="shrink-0 font-mono tabular-nums text-[var(--color-text-secondary)] sm:w-24">
                {formatNumber(area.total)}
              </span>

              <span className="min-w-0 flex-1 text-[var(--color-text-tertiary)]">
                {area.lead ? (
                  <>
                    {kind && <span className="text-[var(--color-text-secondary)]">{kind} · </span>}
                    <span className="text-[var(--color-text-secondary)]">{area.lead.title}</span>
                    <span className="mt-0.5 block truncate text-[11px] [text-wrap:pretty]">
                      {/* The composition wins the second line where there is
                          one: knowing that most of a security count is git
                          history rather than the working tree changes what the
                          number means, and changes it more than one more
                          example would. */}
                      {area.detail || area.lead.description}
                    </span>
                  </>
                ) : (
                  area.detail && (
                    <span className="block truncate text-[11px] [text-wrap:pretty]">
                      {area.detail}
                    </span>
                  )
                )}
              </span>

              <span
                aria-hidden
                className="hidden shrink-0 self-center text-[var(--color-text-tertiary)] transition-transform group-hover:translate-x-0.5 group-hover:text-[var(--color-accent-primary)] sm:block"
              >
                →
              </span>
            </A>
          </li>
        );
      })}
    </ul>
  );
}
