import { getTranslations } from "next-intl/server";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { formatDate } from "@repowise-dev/ui/lib/format";
import { Badge } from "@repowise-dev/ui/ui/badge";
import { listEvents, type CloudEvent } from "@/lib/api/events";
import { evidenceWord, type ChangesTranslator } from "@/lib/changes";

const SEVERITY_VARIANT: Record<string, "stale" | "outline"> = {
  critical: "stale",
  high: "stale",
};

function field(event: CloudEvent, key: string): string {
  const value = event.data[key];
  return typeof value === "string" ? value : "";
}

/**
 * The Commits page's `?view=events`: typed events between consecutive index
 * runs, newest first. An empty list says why (the server's `empty_reason`), so
 * a baseline-only index never reads as a quiet one.
 */
export async function EventsView({ repoId }: { repoId: string }) {
  const t = (await getTranslations("views.events")) as unknown as ChangesTranslator;
  // The API reads oldest first, up to its 1000 ceiling; shown newest first.
  // Events are a few per index run, so the ceiling is years of history. Past
  // it the list needs the API to page newest first.
  const page = await listEvents(repoId, { limit: 1000 }).catch(() => null);

  if (page === null) {
    // A failed read is an error, never an empty (quiet-looking) list.
    return <ApiError title={t("loadErrorTitle")} message={t("loadErrorBody")} />;
  }
  if (page.events.length === 0) {
    const reason = page.empty_reason ?? "baseline_only";
    const key = { baseline_only: "baselineOnly", nothing_moved: "nothingMoved", no_match: "noMatch" }[reason];
    return <EmptyState title={t("emptyTitle")} description={t(key, { runs: page.snapshot_runs ?? 0 })} />;
  }
  return (
    <>
      <ul className="divide-y divide-[var(--color-border-default)] text-xs">
        {[...page.events].reverse().map((e) => {
          const kind = field(e, "repowise.kind");
          const severity = field(e, "repowise.severity");
          const component = field(e, "repowise.component");
          return (
            <li key={e.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2">
              <time dateTime={e.time} className="w-full shrink-0 text-[var(--color-text-tertiary)] sm:w-28">
                {formatDate(e.time)}
              </time>
              <span className="min-w-[10rem] flex-1 font-medium text-[var(--color-text-primary)]">
                {evidenceWord(t, `kinds.${kind}`, kind)}
              </span>
              <span className="text-[var(--color-text-secondary)]">{component || t("repository")}</span>
              <Badge variant={SEVERITY_VARIANT[severity] ?? "outline"}>
                {evidenceWord(t, `severities.${severity}`, severity)}
              </Badge>
            </li>
          );
        })}
      </ul>
      <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">{t("count", { count: page.events.length })}</p>
    </>
  );
}
