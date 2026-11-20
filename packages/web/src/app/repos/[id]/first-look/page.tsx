import type { Metadata } from "next";
import { FirstLookView } from "@/components/reports/first-look-view";

export const metadata: Metadata = { title: "Repo report" };

/** `/repos/[id]/first-look`: the repo report, measured against the first look. */
export default async function FirstLookPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <FirstLookView repoId={id} />;
}
