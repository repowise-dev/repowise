import { redirect } from "next/navigation";

/** Legacy route — security now lives as a Code Health tab. */
export default async function LegacyRedirect({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/repos/${id}/code-health?tab=security`);
}
