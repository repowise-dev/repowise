"use client";

import {
  FreshnessTable,
  type FreshnessTableProps,
} from "@repowise/ui/coverage/freshness-table";
import { regeneratePage } from "@/lib/api/pages";

export function FreshnessTableWithRegenerate({
  pages,
}: Pick<FreshnessTableProps, "pages">) {
  const handleRegenerate = async (pageId: string) => {
    await regeneratePage(pageId);
  };
  return <FreshnessTable pages={pages} onRegenerate={handleRegenerate} />;
}
