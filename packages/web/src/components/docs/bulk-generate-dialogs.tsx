"use client";

import { Sparkles } from "lucide-react";
import { useMemo } from "react";
import { cn } from "@/lib/utils/cn";
import { isWrittenByModel } from "@repowise-dev/ui/lib/page-types";
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
 * One calm line at the docs landing while no page carries model prose yet,
 * typically an index built without a model key. It says what the reader is
 * looking at and offers the same bulk action as the bar, where the reader
 * meets the question rather than only in the header.
 */
export function NoProseNotice({
  pages,
  count,
  running,
  onClick,
  className,
}: {
  /** The page list; the line renders only while none carries model prose. */
  pages: readonly { page_type?: string; provider_name?: string; metadata?: Record<string, unknown> }[];
  /** Pages still waiting for prose. */
  count: number;
  running: boolean;
  onClick: () => void;
  className?: string;
}) {
  const t = useTranslations("docs");
  const noProse = useMemo(() => count > 0 && !pages.some(isWrittenByModel), [count, pages]);
  if (!noProse) return null;
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-[var(--color-text-secondary)]",
        className,
      )}
    >
      <p className="min-w-0">{t("explorer.noProse", { count })}</p>
      <button
        type="button"
        onClick={onClick}
        disabled={running}
        className="inline-flex shrink-0 items-center gap-1 font-medium text-[var(--color-model)] hover:text-[var(--color-model-hover)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-model)] disabled:opacity-60"
      >
        <Sparkles aria-hidden className="h-3.5 w-3.5" />
        {t("bulk.buttonShort")}
      </button>
    </div>
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
