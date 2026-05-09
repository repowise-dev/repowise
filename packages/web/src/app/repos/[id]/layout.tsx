import { redirect } from "next/navigation";
import { getRepo } from "@/lib/api/repos";
import { ActiveJobBannerWrapper as ActiveJobBanner } from "@/components/dashboard/active-job-banner-wrapper";
import { PageTransition } from "@/components/layout/page-transition";
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
      <ActiveJobBanner repoId={id} />
      <RepoBreadcrumb repoName={repoName} />
      <PageTransition>{children}</PageTransition>
    </>
  );
}
