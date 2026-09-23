import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { BarChart3 } from "lucide-react";
import { getStatsHighlights } from "@/lib/api/stats";
import { PageShell } from "@repowise-dev/ui/shared";
import { StatsView } from "@/components/stats/stats-view";

export const metadata: Metadata = { title: "Stats" };

interface Props {
  params: Promise<{ id: string }>;
}

export default async function StatsPage({ params }: Props) {
  const { id } = await params;

  let data;
  try {
    data = await getStatsHighlights(id);
  } catch {
    notFound();
  }

  return (
    <PageShell
      title="By the Numbers"
      icon={<BarChart3 className="h-5 w-5" />}
      description="The things about this codebase you can't see anywhere else: how big it got, when it started, when the work happens, and the records it holds."
      maxWidth="wide"
    >
      <StatsView data={data} repoId={id} />
    </PageShell>
  );
}
