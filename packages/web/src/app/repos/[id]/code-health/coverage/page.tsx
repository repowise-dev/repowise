import { redirect } from "next/navigation";

/** Coverage is a tab of the consolidated Code Health section now. */
export default async function LegacyRedirect({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/repos/${id}/code-health?tab=coverage`);
}
