import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getSymbolDetail } from "@/lib/api/symbols";
import { SymbolPage } from "@repowise-dev/ui/symbols";
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
  } catch {
    notFound();
  }

  return (
    <div className="p-4 sm:p-6 max-w-[1100px]">
      <SymbolPage data={detail} repoId={id} />
    </div>
  );
}
