import { redirect } from "next/navigation";

/** Legacy route; test coverage has its own page. */
export default async function LegacyRedirect({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/repos/${id}/tests`);
}
