import type { Metadata } from "next";
import { PageFrame } from "@repowise-dev/ui/shared/page-shell";
import { FirstLookView } from "@/components/reports/first-look-view";

export const metadata: Metadata = { title: "First look" };

/** `/repos/[id]/first-look`: the report written after the first index. */
export default async function FirstLookPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <PageFrame>
      <FirstLookView repoId={id} />
    </PageFrame>
  );
}
