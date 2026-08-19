import type { Metadata } from "next";
import { Files } from "lucide-react";
import { PageShell } from "@repowise-dev/ui/shared/page-shell";
import { FilesExplorer } from "@/components/files/files-explorer";

export const metadata: Metadata = { title: "Files" };

export default async function FilesIndexPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <PageShell
      maxWidth="wide"
      icon={<Files className="h-5 w-5 text-[var(--color-accent-primary)]" />}
      title="Files"
      description="Drill the map to see how the tree is shaped and where health is thin, then filter the table to find a specific file."
    >
      <FilesExplorer repoId={id} />
    </PageShell>
  );
}
