import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { GitCommitHorizontal } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { ByChangeView, type ByChangeParams } from "@/components/commits/by-change-view";
import { CommitsView } from "@/components/commits/commits-view";
import { parseCommitsView, reviewHref } from "@/lib/changes";
import { getTranslations } from "next-intl/server";

export const metadata: Metadata = { title: "Commits" };

function list(value: string | string[] | undefined): string[] {
  return value === undefined ? [] : Array.isArray(value) ? value : [value];
}

/**
 * The Commits page: the history, led by what the work did to code health,
 * one row per commit (`CommitsView`) or, with `?view=changes`, per merged
 * change (`ByChangeView`); the list's own switch picks the grouping. A change
 * about to land is reviewed on the Review page: `?view=review` links (with
 * `file` or `change`) land there. The history views are server components;
 * what stays interactive is small islands sharing state through the URL.
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
  if (sp.view === "review") {
    const change = list(sp.change)[0];
    redirect(reviewHref(id, { file: list(sp.file), ...(change ? { change } : {}) }));
  }
  const view = parseCommitsView(sp.view);
  const t = await getTranslations("views.commits");
  const description = view === "changes" ? (await getTranslations("views.changes"))("description") : t("description");

  return (
    <PageShell icon={<GitCommitHorizontal />} title={t("title")} description={description}>
      {view === "changes" ? <ByChangeView repoId={id} params={sp} /> : <CommitsView repoId={id} searchParams={sp} />}
    </PageShell>
  );
}
