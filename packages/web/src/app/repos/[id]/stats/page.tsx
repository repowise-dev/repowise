import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { BarChart3 } from "lucide-react";
import { getStatsHighlights } from "@/lib/api/stats";
import { PageShell } from "@repowise-dev/ui/shared";
import { StatsView } from "@/components/stats/stats-view";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("views.stats");
  return { title: t("title") };
}

interface Props {
  params: Promise<{ id: string }>;
}

export default async function StatsPage({ params }: Props) {
  const { id } = await params;
  const t = await getTranslations("views.stats");

  let data;
  try {
    data = await getStatsHighlights(id);
  } catch {
    notFound();
  }

  return (
    <PageShell
      title={t("title")}
      icon={<BarChart3 className="h-5 w-5" />}
      description={t("description")}
      maxWidth="wide"
    >
      <StatsView data={data} repoId={id} />
    </PageShell>
  );
}
