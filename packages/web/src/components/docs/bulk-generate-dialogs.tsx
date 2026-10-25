"use client";

import { Sparkles } from "lucide-react";
import { Spinner } from "@repowise-dev/ui/ui/spinner";
import { BulkGenerateConfirm } from "./bulk-generate-confirm";
import { BulkGenerateProgress } from "./bulk-generate-progress";
import { type useBulkGenerate } from "@/lib/hooks/use-bulk-generate";
import { useTranslations } from "next-intl";

/**
 * The bar's one model action: write every subsystem page still built from the
 * index. Renders only while such pages remain (`count > 0`). On a
 * phone the label shortens; the count stays in the accessible name.
 */
export function BulkWriteButton({
  count,
  running,
  onClick,
}: {
  count: number;
  running: boolean;
  onClick: () => void;
}) {
  const t = useTranslations("docs");
  if (count <= 0) return null;
  const label = t("bulk.button", { count });
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={running}
      aria-label={label}
      className="flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-[var(--color-model)]/40 bg-[var(--color-model-muted)] px-2 text-xs font-medium text-[var(--color-model)] transition-colors hover:text-[var(--color-model-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-model)] disabled:opacity-60 sm:px-2.5"
    >
      {running ? <Spinner size="sm" /> : <Sparkles aria-hidden className="h-3.5 w-3.5" />}
      <span className="sm:hidden">{t("bulk.buttonShort")}</span>
      <span className="hidden sm:inline">{label}</span>
    </button>
  );
}

/**
 * The confirm and progress dialogs behind `BulkWriteButton`. Mounted on their
 * own so a run's progress outlives the button, which the bar drops once no
 * subsystem page is left to write.
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
