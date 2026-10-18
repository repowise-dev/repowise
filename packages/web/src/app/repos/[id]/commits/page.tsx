import type { Metadata } from "next";
import { GitCommitHorizontal } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { CommitsViewToggle } from "@/components/commits/commits-view-toggle";
import { ByChangeView, type ByChangeParams } from "@/components/commits/by-change-view";
import { CommitsView } from "@/components/commits/commits-view";
import { EventsView } from "@/components/commits/events-view";
import { ImpactTab } from "@/components/risk/impact-tab";
import { parseCommitsView } from "@/lib/changes";
import { getTranslations } from "next-intl/server";

export const metadata: Metadata = { title: "Commits" };

/**
 * The Commits page: one shell, one view at a time. The commit view
 * (`CommitsView`) is the default and leads with what the work did to code
 * health; `?view=changes` groups the history by merged change
 * (`ByChangeView`), `?view=events` lists what moved between index runs
 * (`EventsView`), `?view=review` shows a change's blast radius before it
 * lands (`ImpactTab`). Each view is a server component; what stays interactive is
 * small islands sharing state through the URL.
 */
export default async function CommitsPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<ByChangeParams & Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const view = parseCommitsView(sp.view);
  const t = await getTranslations("views.commits");
  const description =
    view === "changes"
      ? (await getTranslations("views.changes"))("description")
      : view === "events"
        ? (await getTranslations("views.events"))("description")
        : view === "review"
          ? t("reviewDescription")
          : t("description");

  return (
    <PageShell
      icon={<GitCommitHorizontal />}
      title={t("title")}
      description={description}
      actions={<CommitsViewToggle repoId={id} view={view} />}
    >
      {view === "changes" ? (
        <ByChangeView repoId={id} params={sp} />
      ) : view === "events" ? (
        <EventsView repoId={id} />
      ) : view === "review" ? (
        <ImpactTab repoId={id} />
      ) : (
        <CommitsView repoId={id} searchParams={sp} />
      )}
    </PageShell>
  );
}
