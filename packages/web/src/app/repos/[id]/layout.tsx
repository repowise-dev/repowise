import { redirect } from "next/navigation";
import { getRepo } from "@/lib/api/repos";
import { ActiveJobBannerWrapper as ActiveJobBanner } from "@/components/dashboard/active-job-banner-wrapper";
import { PageTransition } from "@/components/layout/page-transition";
import { ReindexHintBanner } from "@/components/layout/reindex-hint-banner";
import { RepoBreadcrumb } from "@/components/layout/repo-breadcrumb";

interface RepoLayoutProps {
  children: React.ReactNode;
  params: Promise<{ id: string }>;
}

export default async function RepoLayout({ children, params }: RepoLayoutProps) {
  const { id } = await params;
  let repoName = id;
  try {
    const repo = await getRepo(id);
    repoName = repo.name;
  } catch {
    redirect("/");
  }
  return (
    <>
      <ReindexHintBanner repoId={id} />
      <ActiveJobBanner repoId={id} />
      <RepoBreadcrumb repoName={repoName} />
      <PageTransition>{children}</PageTransition>
    </>
  );
}
