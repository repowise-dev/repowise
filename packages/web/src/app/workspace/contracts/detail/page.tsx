import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ApiClientError } from "@/lib/api/client";
import { getWorkspace, getWorkspaceContractDetail } from "@/lib/api/workspace";
import { ContractBody, headingFor } from "./contract-body";

export const revalidate = 30;

type Props = {
  searchParams: Promise<{ repo?: string; file?: string; id?: string }>;
};

export async function generateMetadata({ searchParams }: Props): Promise<Metadata> {
  const { repo, file, id } = await searchParams;
  if (!repo || !file || !id) return { title: "Contract" };
  try {
    const detail = await getWorkspaceContractDetail({ repo, file, id });
    return { title: `${headingFor(detail.contract)} — Contract` };
  } catch {
    return { title: "Contract" };
  }
}

/**
 * One contract.
 *
 * A route rather than a modal: the identity is three query parameters, which
 * makes it the thing somebody pastes to a teammate, and the list page already
 * keeps its own state in the URL so the two read the same way.
 *
 * The workspace payload comes along for one reason: contracts name their repo
 * by alias, and a file link needs the indexed repo id. A repo that has never
 * been indexed has no id, which is why the map is passed down whole rather
 * than resolved into hrefs here — the degrade belongs next to the link.
 */
export default async function ContractDetailPage({ searchParams }: Props) {
  const { repo, file, id } = await searchParams;
  if (!repo || !file || !id) notFound();

  const [detailResult, ws] = await Promise.allSettled([
    getWorkspaceContractDetail({ repo, file, id }),
    getWorkspace(),
  ]);

  if (detailResult.status === "rejected") {
    const err = detailResult.reason;
    // A missing contract and a workspace with no contract data both answer 404,
    // and both mean this page has nothing to draw. Anything else is a real
    // failure and belongs on the error boundary, not behind a "not found".
    if (err instanceof ApiClientError && err.status === 404) notFound();
    throw err;
  }

  const repoIds: Record<string, string> = {};
  if (ws.status === "fulfilled") {
    for (const r of ws.value.repos) {
      if (r.repo_id) repoIds[r.alias] = r.repo_id;
    }
  }

  return <ContractBody detail={detailResult.value} repoIds={repoIds} />;
}
