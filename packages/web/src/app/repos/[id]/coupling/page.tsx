import { redirect } from "next/navigation";

/** Legacy route — change coupling now lives as an Architecture tab. */
export default async function LegacyRedirect({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/repos/${id}/architecture?view=coupling`);
}
