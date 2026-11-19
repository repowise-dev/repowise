import type { Metadata } from "next";
import { PageFrame } from "@repowise-dev/ui/shared/page-shell";
import { DocsHeader } from "@/components/docs/docs-header";
import { DocDriftTab } from "@/components/risk/doc-drift-tab";

export const metadata: Metadata = { title: "Doc drift" };

/**
 * `/repos/[id]/docs/drift`: documentation lines that name files the
 * repository no longer has. `?document=` opens narrowed to one document.
 */
export default async function DocDriftPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ document?: string | string[] }>;
}) {
  const { id } = await params;
  const { document } = await searchParams;
  return (
    <div className="flex h-full flex-col">
      <DocsHeader />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <PageFrame maxWidth="wide">
          <DocDriftTab
            repoId={id}
            initialDocument={typeof document === "string" ? document : undefined}
          />
        </PageFrame>
      </div>
    </div>
  );
}
