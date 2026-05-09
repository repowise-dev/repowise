import { redirect } from "next/navigation";

export default async function CoverageRedirect({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/repos/${id}/docs/coverage`);
}
