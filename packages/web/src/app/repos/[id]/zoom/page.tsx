import { redirect } from "next/navigation";

/**
 * Legacy route: the zoom map now lives at `/knowledge-graph` (it replaced the
 * old node-link view under that name). Keep old `/zoom` links working, forwarding
 * a `?focus=` deep-link if present.
 */
export default async function ZoomRedirect({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ focus?: string }>;
}) {
  const { id } = await params;
  const { focus } = await searchParams;
  const qs = focus ? `?focus=${encodeURIComponent(focus)}` : "";
  redirect(`/repos/${id}/knowledge-graph${qs}`);
}
