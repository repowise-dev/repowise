import type { Metadata } from "next";
import { GitCommitHorizontal } from "lucide-react";
import { CommitsExplorer } from "@/components/commits/commits-explorer";

export const metadata: Metadata = { title: "Commits" };

export default async function CommitsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <div>
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
          <GitCommitHorizontal className="h-5 w-5 text-[var(--color-accent-primary)]" />
          Commits
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          A review-priority queue — every indexed commit scored for change-risk and ranked
          relative to this repo&apos;s own history. Open a commit for its per-feature breakdown.
        </p>
      </div>

      <CommitsExplorer repoId={id} />
    </div>
  );
}
