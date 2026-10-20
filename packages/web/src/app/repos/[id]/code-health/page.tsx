import { redirect } from "next/navigation";
import { CodeHealthView } from "./code-health-view";
import { movedTabHref } from "./tabs";

/**
 * `/repos/[id]/code-health`. A server shell so a link to a tab that moved out
 * (Tests, Security, Doc drift, blast radius) redirects before anything
 * paints, instead of flashing the Map first.
 */
export default async function CodeHealthPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(await searchParams)) {
    for (const v of Array.isArray(value) ? value : value === undefined ? [] : [value]) {
      sp.append(key, v);
    }
  }
  const moved = movedTabHref(id, sp);
  if (moved) redirect(moved);
  return <CodeHealthView />;
}
