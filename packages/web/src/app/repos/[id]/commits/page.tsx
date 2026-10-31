import type { Metadata } from "next";
import { GitCommitHorizontal } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { CommitsViewToggle } from "@/components/commits/commits-view-toggle";
import { ByChangeView, type ByChangeParams } from "@/components/commits/by-change-view";
import { CommitsView } from "@/components/commits/commits-view";
import { ImpactTab } from "@/components/risk/impact-tab";
import { parseCommitsView } from "@/lib/changes";
import { getTranslations } from "next-intl/server";

export const metadata: Metadata = { title: "Commits" };

/**
 * The Commits page: one shell, two lenses. History is the default and leads
 * with what the work did to code health, one row per commit (`CommitsView`)
 * or, with `?view=changes`, per merged change (`ByChangeView`); the list's
 * own switch picks the grouping. `?view=review` shows a change's blast radius
 * before it lands (`ImpactTab`). Every other `?view=` value lands on the
 * history. The history views are server components; what stays interactive is
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
      ) : view === "review" ? (
        <ImpactTab repoId={id} />
      ) : (
        <CommitsView repoId={id} searchParams={sp} />
      )}
    </PageShell>
  );
}
