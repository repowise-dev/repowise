import { redirect } from "next/navigation";

/** Legacy route — the C4 diagram is superseded by the curated Knowledge Graph. */
export default async function LegacyC4Redirect({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/repos/${id}/knowledge-graph`);
}
