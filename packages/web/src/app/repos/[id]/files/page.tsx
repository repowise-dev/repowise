import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Files } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { FilesExplorer } from "@/components/files/files-explorer";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("views.files");
  return { title: t("title") };
}

export default async function FilesIndexPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const t = await getTranslations("views.files");

  return (
    <PageShell
      maxWidth="wide"
      icon={<Files className="h-5 w-5 text-[var(--color-accent-primary)]" />}
      title={t("title")}
      description={t("description")}
    >
      <FilesExplorer repoId={id} />
    </PageShell>
  );
}
