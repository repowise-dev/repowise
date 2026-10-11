import { redirect } from "next/navigation";

/** Legacy route — the symbol index now lives under Architecture. */
export default async function LegacySymbolsRedirect({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/repos/${id}/architecture?view=symbols`);
}
