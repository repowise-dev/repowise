import { ArrowRight } from "lucide-react";
import type { FirstLookLivingReport } from "@repowise-dev/types/first-look";
import { formatDate, formatNumber } from "../../lib/format";
import { renderActionTitle as inline } from "../../overview/next-actions";
import { FIRST_LOOK_COPY_EN, type FirstLookCopy } from "./copy";
import { ProgressBar } from "./first-look-report";
import { progress } from "./living";
import type { LinkLike } from "./sections";

/**
 * One line at the top of Overview: progress since the first look, the most
 * urgent open item, and the way into the report. The full list lives on the
 * report page. A low-intensity accent rule marks it without a card.
 */
export function RepoReportCard({
  report,
  href,
  copy = FIRST_LOOK_COPY_EN,
  LinkComponent,
}: {
  report: FirstLookLivingReport;
  href: string;
  copy?: FirstLookCopy;
  LinkComponent?: LinkLike;
}) {
  const Link: LinkLike = LinkComponent ?? "a";
  const first = report.baseline?.stamp ?? null;
  const p = progress(report);
  const leadId = report.top[0];
  const lead = leadId
    ? (report.prose?.items?.[leadId] ?? report.sections.flatMap((s) => s.items).find((i) => i.id === leadId)?.rule)
    : undefined;
  const b = (n: number) => <b className="font-semibold tabular-nums text-[var(--color-text-primary)]">{formatNumber(n)}</b>;
  return (
    <section
      aria-labelledby="repo-report-card"
      className="flex min-h-10 flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-l-2 border-[var(--color-border-default)] border-l-[color-mix(in_srgb,var(--color-accent-primary)_55%,transparent)] px-3 py-2 text-[13px] text-[var(--color-text-secondary)]"
    >
      <h3 id="repo-report-card" className="m-0 text-[13px] font-semibold text-[var(--color-text-primary)]">
        {copy.repoReport}
      </h3>
      {first ? (
        <>
          <ProgressBar p={p} className="hidden h-1 w-16 sm:flex" />
          <span>
            {b(p.resolved)} {copy.resolved}
            {p.acknowledged ? (
              <>
                {" · "}
                {b(p.acknowledged)} {copy.acknowledged}
              </>
            ) : null}
            {" · "}
            {b(p.added)} {copy.newSinceDate(formatDate(first.created_at))}
          </span>
        </>
      ) : null}
      {lead ? (
        // Phones drop the item so the line wraps to two at most.
        <span
          className="hidden min-w-0 flex-1 truncate text-[var(--color-text-tertiary)] sm:block"
          title={lead.split("`").join("")}
        >
          {inline(lead)}
        </span>
      ) : null}
      <Link
        href={href}
        className="ml-auto inline-flex items-center gap-1 rounded-sm font-medium text-[var(--color-accent-primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
      >
        {copy.openReport}
        <ArrowRight aria-hidden className="size-3.5" />
      </Link>
    </section>
  );
}
