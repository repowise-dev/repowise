import type { Metadata } from "next";
import { FirstLookView } from "@/components/reports/first-look-view";

export const metadata: Metadata = { title: "Repo report" };

/** `/repos/[id]/report`: the repo report, measured against the first look. */
export default async function RepoReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <FirstLookView repoId={id} />;
}
