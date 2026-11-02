import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getSymbolDetail } from "@/lib/api/symbols";
import { ApiClientError } from "@/lib/api/client";
import { PageFrame } from "@repowise-dev/ui/shared/page-shell";
import { SymbolPage } from "@repowise-dev/ui/symbols";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import type { SymbolDetailResponse } from "@repowise-dev/types/symbols";

interface Props {
  params: Promise<{ id: string; symbolId: string }>;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { symbolId } = await params;
  const decoded = decodeURIComponent(symbolId);
  return { title: decoded.split("::").pop() ?? decoded };
}

export default async function SymbolEntityPage({ params }: Props) {
  const { id, symbolId } = await params;
  const decoded = decodeURIComponent(symbolId);

  let detail: SymbolDetailResponse;
  try {
    detail = await getSymbolDetail(id, decoded);
  } catch (err) {
    if (err instanceof ApiClientError && err.status === 404) notFound();
    throw err;
  }

  const filePath = detail.symbol.file_path;

  return (
    <PageFrame>
      <SymbolPage
        data={detail}
        repoId={id}
        LinkComponent={Link}
        breadcrumb={[
          { label: filePath.split("/").pop() || filePath, href: fileEntityPath(`/repos/${id}`, filePath) },
          { label: detail.symbol.name },
        ]}
      />
    </PageFrame>
  );
}
