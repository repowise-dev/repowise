import { redirect } from "next/navigation";

/** Legacy route — the knowledge graph now lives under Architecture. */
export default async function LegacyC4Redirect({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const qs = new URLSearchParams({ view: "c4" });
  for (const [key, value] of Object.entries(sp)) {
    if (typeof value !== "string") continue;
    // The c4 view's own "view" param (overview/groups/detail) was renamed to
    // "c4view" when the page moved under Architecture.
    if (key === "view") qs.set("c4view", value);
    else qs.set(key, value);
  }
  redirect(`/repos/${id}/architecture?${qs.toString()}`);
}
