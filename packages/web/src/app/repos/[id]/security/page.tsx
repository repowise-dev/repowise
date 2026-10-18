import type { Metadata } from "next";
import { Shield } from "lucide-react";
import { getTranslations } from "next-intl/server";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { SecurityRescanButton, SecurityView } from "@/components/security/security-view";

export const metadata: Metadata = { title: "Security" };

/** `/repos/[id]/security`: the posture first, then every finding. */
export default async function SecurityPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const t = await getTranslations("views.security");
  return (
    <PageShell
      icon={<Shield />}
      title={t("title")}
      description={t("description")}
      actions={<SecurityRescanButton repoId={id} />}
    >
      <SecurityView repoId={id} />
    </PageShell>
  );
}
