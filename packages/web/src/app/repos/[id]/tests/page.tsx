import type { Metadata } from "next";
import { FlaskConical } from "lucide-react";
import { getTranslations } from "next-intl/server";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { TestsView } from "@/components/tests/tests-view";

export const metadata: Metadata = { title: "Tests" };

/**
 * `/repos/[id]/tests`: test coverage, measured from an ingested report or
 * inferred from the graph. Named for tests rather than coverage because
 * `/docs/coverage` already means documentation freshness.
 */
export default async function TestsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const t = await getTranslations("views.tests");
  return (
    <PageShell icon={<FlaskConical />} title={t("title")} description={t("description")}>
      <TestsView repoId={id} />
    </PageShell>
  );
}
