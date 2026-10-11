import { redirect } from "next/navigation";

/** Legacy route — Risk merged into Code Health. Tab names map 1:1. */
export default async function LegacyRiskRedirect({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const tab = typeof sp.tab === "string" ? sp.tab : "heatmap";
  redirect(`/repos/${id}/code-health?tab=${encodeURIComponent(tab)}`);
}
