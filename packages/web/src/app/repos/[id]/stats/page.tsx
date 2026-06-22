import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { BarChart3 } from "lucide-react";
import { getStatsHighlights } from "@/lib/api/stats";
import { PageShell } from "@repowise-dev/ui/shared";
import { StatsTabs } from "@/components/stats/stats-tabs";

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
      description="A tour of everything the index knows about this codebase — scale, growth, people, and the signals only repowise computes."
      maxWidth="wide"
    >
      <StatsTabs data={data} />
    </PageShell>
  );
}
