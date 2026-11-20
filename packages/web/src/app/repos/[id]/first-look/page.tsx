import { redirect } from "next/navigation";

/** The repo report's first address: links written before it was renamed still land on it. */
export default async function FirstLookRedirect({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(`/repos/${id}/report`);
}
