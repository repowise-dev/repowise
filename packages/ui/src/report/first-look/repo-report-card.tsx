import { ArrowUpRight } from "lucide-react";
import type { FirstLookLivingItem, FirstLookLivingReport } from "@repowise-dev/types/first-look";
import { formatDate } from "../../lib/format";
import { renderActionTitle as inline } from "../../overview/next-actions";
import { BUTTON } from "./controls";
import { FIRST_LOOK_COPY_EN, type FirstLookCopy } from "./copy";
import { ProgressLine } from "./first-look-report";
import { progress } from "./living";
import type { LinkLike } from "./sections";

/** The card at the top of Overview: progress since the first look, the top three, and the way in. */
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
  const byId = new Map(report.sections.flatMap((s) => s.items).map((i) => [i.id, i]));
  const top = report.top.map((id) => byId.get(id)).filter((i): i is FirstLookLivingItem => i != null);
  const items = report.prose?.items ?? {};
  const first = report.baseline?.stamp ?? null;
  const since = first ? copy.newSinceDate(formatDate(first.created_at)) : null;
  return (
    <section
      aria-labelledby="repo-report-card"
      className="grid gap-2.5 rounded-xl border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4"
    >
      <div className="flex flex-wrap items-center gap-x-[18px] gap-y-2.5">
        <h3 id="repo-report-card" className="m-0 text-[15px] font-semibold">
          {copy.repoReport}
        </h3>
        {first ? <ProgressLine p={progress(report)} copy={copy} tail={since ?? undefined} /> : null}
        <span className="flex-1" />
        <Link href={href} className={BUTTON}>
          {copy.openReport}
          <ArrowUpRight aria-hidden className="size-4" />
        </Link>
      </div>
      {top.length ? (
        <ol className="m-0 grid list-decimal gap-0.5 pl-[18px] text-[13px] text-[var(--color-text-secondary)]">
          {top.map((item) => (
            <li key={item.id}>{inline(items[item.id] ?? item.rule)}</li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}
