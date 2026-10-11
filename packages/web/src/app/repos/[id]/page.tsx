import { redirect } from "next/navigation";

/** Repo root — Overview is the landing page; Chat lives at /chat. */
export default async function RepoRootPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/repos/${id}/overview`);
}
