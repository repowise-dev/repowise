import { redirect } from "next/navigation";

/** The refactoring-target ranking merged into the Triage fix-next queue. */
export default async function LegacyRedirect({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/repos/${id}/code-health`);
}
