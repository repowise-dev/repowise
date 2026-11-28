"use client";

import { Sparkles } from "lucide-react";
import { useMemo } from "react";
import { cn } from "@/lib/utils/cn";
import { isWrittenByModel } from "@repowise-dev/ui/lib/page-types";
import { Spinner } from "@repowise-dev/ui/ui/spinner";
import { AiPromptButton } from "@repowise-dev/ui/health/ai-prompt-button";
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
 * The docs landing's model ask while no page carries model prose yet,
 * typically an index built without a model. It says what a model adds and
 * offers the same bulk action as the bar. When a key or local model is found
 * on this machine (`ready`), the agent prompt for writing the docs leads.
 */
export function NoProseNotice({
  pages,
  count,
  running,
  onClick,
  ready = null,
  className,
}: {
  /** The page list; the line renders only while none carries model prose. */
  pages: readonly { page_type?: string; provider_name?: string; metadata?: Record<string, unknown> }[];
  /** Pages still waiting for prose. */
  count: number;
  running: boolean;
  onClick: () => void;
  /** A model source found here and its agent prompt (`docs_model_ready`). */
  ready?: { source: string; prompt: string | null } | null;
  className?: string;
}) {
  const t = useTranslations("docs");
  const noProse = useMemo(() => count > 0 && !pages.some(isWrittenByModel), [count, pages]);
  if (!noProse) return null;
  const prompt = ready?.prompt;
  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-lg border border-[color-mix(in_srgb,var(--color-model)_30%,transparent)] bg-[var(--color-model-muted)] px-3 py-2.5 text-xs text-[var(--color-text-secondary)]",
        className,
      )}
    >
      <div className="min-w-0">
        <p className="text-sm text-[var(--color-text-primary)]">{t("explorer.modelAsk")}</p>
        <p className="mt-0.5">
          {ready ? `${t("explorer.modelReady", { source: ready.source })} ` : ""}
          {t("explorer.modelAskSub", { count })}
        </p>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-3">
        {prompt && (
          <AiPromptButton
            label={t("explorer.copyPrompt")}
            copiedLabel={t("explorer.copied")}
            copyText={() => prompt}
          />
        )}
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
