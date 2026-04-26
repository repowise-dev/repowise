import { redirect } from "next/navigation";
import { getRepo } from "@/lib/api/repos";
import { ActiveJobBanner } from "@/components/dashboard/active-job-banner";
import { PageTransition } from "@/components/layout/page-transition";

interface RepoLayoutProps {
  children: React.ReactNode;
  params: Promise<{ id: string }>;
}

export default async function RepoLayout({ children, params }: RepoLayoutProps) {
  const { id } = await params;
  try {
    await getRepo(id);
  } catch {
    // Repo doesn't exist in this database (e.g. stale URL from a previous
    // project). Redirect to the dashboard so the user sees what's available.
    redirect("/");
  }
  return (
    <>
      <ActiveJobBanner repoId={id} />
      <PageTransition>{children}</PageTransition>
    </>
  );
}
