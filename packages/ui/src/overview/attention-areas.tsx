import * as React from "react";
import type { OverviewAttentionAreaRow } from "@repowise-dev/types/overview";
import { attentionSourceHref, attentionSourceLabel } from "../dashboard/attention-href";
import { biomarkerLabel } from "../health/biomarker-glossary";
import { SeverityMark } from "../health/severity-mark";
import { FilePath } from "../shared/file-path";
import { formatNumber } from "../lib/format";

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
 * to re-read it every time. The worst item's severity sits beside the lead as
 * a dot and a word, read once by sight and once by a screen reader.
 */
export function AttentionAreas({
  areas,
  prefix,
  LinkComponent,
}: {
  /** `unit` names what the count is in, e.g. "open findings". */
  areas: (OverviewAttentionAreaRow & { unit?: string })[];
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
              <span className="shrink-0 font-medium text-[var(--color-text-primary)] group-hover:text-[var(--color-accent-primary)] sm:w-32">
                {attentionSourceLabel(area.key)}
              </span>

              <span className="shrink-0 text-[var(--color-text-tertiary)] sm:w-48">
                <span className="font-mono tabular-nums text-[var(--color-text-secondary)]">
                  {formatNumber(area.total)}
                </span>
                {area.unit && ` ${area.unit}`}
              </span>

              <span className="min-w-0 flex-1 text-[var(--color-text-tertiary)]">
                {area.lead ? (
                  <>
                    <SeverityMark severity={area.severity} className="mr-2" />
                    {kind && <span className="text-[var(--color-text-secondary)]">{kind} · </span>}
                    {area.lead.target_id && area.lead.title === area.lead.target_id ? (
                      <FilePath
                        path={area.lead.title}
                        fold
                        className="min-w-0 align-baseline text-[var(--color-text-secondary)]"
                      />
                    ) : (
                      <span className="text-[var(--color-text-secondary)]">{area.lead.title}</span>
                    )}
                    <span className="mt-0.5 block truncate text-[11px] [text-wrap:pretty]">
                      {/* The composition wins the second line where there is
                          one: knowing that most security matches sit in tests
                          and docs changes what the number means more than one
                          more example would. */}
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
