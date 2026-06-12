import { redirect } from "next/navigation";

/** Legacy route — the dependency graph now lives under Architecture. */
export default async function LegacyGraphRedirect({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const qs = new URLSearchParams({ view: "graph" });
  for (const [key, value] of Object.entries(sp)) {
    if (typeof value === "string" && key !== "view") qs.set(key, value);
  }
  redirect(`/repos/${id}/architecture?${qs.toString()}`);
}
