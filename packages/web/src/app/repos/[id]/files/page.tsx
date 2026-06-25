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
      description="Every indexed file, ranked by importance and browsable by folder. Drill into the map or filter the table to jump straight to a file."
    >
      <FilesExplorer repoId={id} />
    </PageShell>
  );
}
