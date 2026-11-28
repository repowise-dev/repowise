import { permanentRedirect } from "next/navigation";

/** The repo report's first address: links written before it was renamed still land on it. */
export default async function FirstLookRedirect({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  permanentRedirect(`/repos/${id}/report`);
}
