import type { Metadata } from "next";
import { GitPullRequestArrow } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { getTranslations } from "next-intl/server";
import { ImpactTab } from "@/components/risk/impact-tab";

export const metadata: Metadata = { title: "Review" };

/**
 * Review: the change about to land. Pick uncommitted work, a branch or a
 * commit and see its reach, what to look at first, the tests to run, its
 * health check, and a prompt that hands the review to an agent. `?change=`
 * (wip, a branch, a full sha) opens one change; `?file=` opens files.
 */
export default async function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const t = await getTranslations("views.review");
  return (
    <PageShell icon={<GitPullRequestArrow />} title={t("title")} description={t("description")}>
      <ImpactTab repoId={id} />
    </PageShell>
  );
}
