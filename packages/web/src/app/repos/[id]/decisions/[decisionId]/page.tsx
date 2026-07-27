import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDecision } from "@/lib/api/decisions";
import { stripMarkdown } from "@repowise-dev/ui/lib/format";
import { DecisionDetail } from "@/components/decisions/decision-detail";

export const revalidate = 30;

interface Props {
  params: Promise<{ id: string; decisionId: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id: repoId, decisionId } = await params;
  try {
    const decision = await getDecision(repoId, decisionId);
    return { title: `${stripMarkdown(decision.title)} — Decision` };
  } catch {
    return { title: "Decision" };
  }
}

/**
 * One decision record.
 *
 * A reading surface: the body is context, rationale and rejected alternatives,
 * which is prose someone reads through rather than scans. `--page-pad` and a
 * 3xl measure, matching the reading column floor rather than the old bare
 * `p-6` with no centring.
 */
export default async function DecisionDetailPage({ params }: Props) {
  const { id: repoId, decisionId } = await params;

  let decision;
  try {
    decision = await getDecision(repoId, decisionId);
  } catch {
    notFound();
  }

  return (
    <div className="mx-auto w-full max-w-3xl p-[var(--page-pad)]">
      <DecisionDetail decision={decision} repoId={repoId} />
    </div>
  );
}
