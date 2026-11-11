import { redirect } from "next/navigation";
import { reviewHref } from "@/lib/changes";

/** Legacy route: blast radius lives on the Review page. */
export default async function LegacyRedirect({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(reviewHref(id));
}
