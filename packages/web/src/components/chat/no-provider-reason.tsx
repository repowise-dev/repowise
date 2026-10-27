"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";

/** The composer's no-provider state: "Add a model" opens the model sheet
 *  (mounted by the chat surface, so it outlives this reason), and the
 *  Settings link stays as the longer way round. */
export function NoProviderReason({
  message,
  onAddModel,
}: {
  /** `noProvider` on the full chat page, `noProviderShort` in the dock. */
  message: "noProvider" | "noProviderShort";
  onAddModel: () => void;
}) {
  const t = useTranslations("chat");
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
    </span>
  );
}
