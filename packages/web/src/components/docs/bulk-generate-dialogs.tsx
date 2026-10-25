"use client";

import { BulkGenerateConfirm } from "./bulk-generate-confirm";
import { BulkGenerateProgress } from "./bulk-generate-progress";
import { type useBulkGenerate } from "@/lib/hooks/use-bulk-generate";
import { useTranslations } from "next-intl";

/**
 * The confirm and progress dialogs behind the docs bar's one bulk action,
 * "Write all subsystem pages" in the More menu. The menu item opens the flow;
 * these stay mounted on their own so a run's progress outlives the item, which
 * the menu drops once no subsystem page is left to write.
 */
export function BulkGenerateDialogs({
  flow,
  repoId,
  onGenerated,
}: {
  flow: ReturnType<typeof useBulkGenerate>;
  repoId: string;
  /** Called once a run completes so the host can refresh the page list. */
  onGenerated?: () => void;
}) {
  const t = useTranslations("docs");
  return (
    <>
      <BulkGenerateConfirm flow={flow} repoId={repoId} title={t("bulk.title")} />
      <BulkGenerateProgress
        flow={flow}
        {...(onGenerated ? { onGenerated } : {})}
      />
    </>
  );
}
