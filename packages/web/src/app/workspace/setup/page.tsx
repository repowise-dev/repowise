import type { Metadata } from "next";
import { ListChecks } from "lucide-react";
import { getTranslations } from "next-intl/server";
import { PageShell } from "@repowise-dev/ui/shared";
import { WorkspaceSetup } from "@/components/setup/workspace-setup";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("setup.workspace");
  return { title: t("title") };
}

/** `/workspace/setup`: every member's setup in one table. The rows load client side. */
export default async function WorkspaceSetupPage() {
  const t = await getTranslations("setup.workspace");
  return (
    <PageShell title={t("title")} icon={<ListChecks />} description={t("description")}>
      <WorkspaceSetup />
    </PageShell>
  );
}
