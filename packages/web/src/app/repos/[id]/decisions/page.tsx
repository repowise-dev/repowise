import { notFound } from "next/navigation";
import { listDecisions } from "@/lib/api/decisions";
import { ApiClientError } from "@/lib/api/client";
import { DecisionsTableWrapper } from "@/components/decisions/decisions-table-wrapper";

export const revalidate = 30;

interface Props {
  params: Promise<{ id: string }>;
}

export default async function DecisionsPage({ params }: Props) {
  const { id: repoId } = await params;

  let decisions;
  try {
    decisions = await listDecisions(repoId, { include_proposed: true, limit: 100 });
  } catch (err) {
    if (err instanceof ApiClientError && err.status === 404) {
      notFound();
    }
    // Re-throw so the nearest error.tsx boundary can surface a retry UI
    throw err;
  }

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold text-[var(--color-text-primary)]">
          Architectural Decisions
        </h1>
        <p className="mt-1 text-sm text-[var(--color-text-tertiary)]">
          Why the codebase is built the way it is — constraints, tradeoffs, and rejected alternatives.
        </p>
      </div>
      <DecisionsTableWrapper repoId={repoId} initialData={decisions} />
    </div>
  );
}
