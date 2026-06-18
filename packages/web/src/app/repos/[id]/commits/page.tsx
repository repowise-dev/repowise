import type { Metadata } from "next";
import { GitCommitHorizontal } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { CommitsExplorer } from "@/components/commits/commits-explorer";

export const metadata: Metadata = { title: "Commits" };

export default async function CommitsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <PageShell
      maxWidth="wide"
      icon={<GitCommitHorizontal className="h-5 w-5 text-[var(--color-accent-primary)]" />}
      title="Commits"
      description="A review-priority queue — every indexed commit scored for change-risk and ranked relative to this repo's own history. Open a commit for its per-feature breakdown."
    >
      <CommitsExplorer repoId={id} />
    </PageShell>
  );
}
