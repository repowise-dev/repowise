"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { AiPromptButton } from "@repowise-dev/ui/health/ai-prompt-button";

/** The composer's no-provider state: "Add a model" opens the model sheet
 *  (mounted by the chat surface, so it outlives this reason), and the
 *  Settings link stays as the longer way round. With a key or local model
 *  found on this machine (`ready`), it names it and offers the agent prompt. */
export function NoProviderReason({
  message,
  onAddModel,
  ready = null,
}: {
  /** `noProvider` on the full chat page, `noProviderShort` in the dock. */
  message: "noProvider" | "noProviderShort";
  onAddModel: () => void;
  /** A model source found here and its agent prompt (`docs_model_ready`). */
  ready?: { source: string; prompt: string | null } | null;
}) {
  const t = useTranslations("chat");
  const prompt = ready?.prompt;
  return (
    <span>
      <button
        type="button"
        onClick={onAddModel}
        className="mr-2 inline-flex items-center rounded-md bg-[var(--color-model)] px-2 py-0.5 text-xs font-semibold text-[var(--color-text-on-model)] hover:bg-[var(--color-model-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-model)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-bg-surface)]"
      >
        {t("addModel")}
      </button>
      {t.rich(message, {
        link: (chunks) => (
          <Link
            href="/settings/models"
            className="text-[var(--color-accent-primary)] hover:underline"
          >
            {chunks}
          </Link>
        ),
      })}
      {ready && <span className="ml-1">{t("modelReady", { source: ready.source })}</span>}
      {prompt && (
        <AiPromptButton
          className="ml-2 align-middle"
          label={t("copyPrompt")}
          copiedLabel={t("copied")}
          copyText={() => prompt}
        />
      )}
    </span>
  );
}
