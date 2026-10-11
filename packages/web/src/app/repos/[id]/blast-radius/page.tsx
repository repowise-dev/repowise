import { redirect } from "next/navigation";

/** Legacy route — redirects into the consolidated IA. */
export default async function LegacyRedirect({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/repos/${id}/code-health?tab=impact`);
}
