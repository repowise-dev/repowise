import { redirect } from "next/navigation";
import { commitsViewHref } from "@/lib/changes";

/** Legacy route: blast radius is the Commits page's "Review a change" view. */
export default async function LegacyRedirect({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(commitsViewHref(id, "review"));
}
