"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import {
  AiPromptButton,
  AiPromptModal,
  buildChangeAiPrompt,
  changePromptFixes,
  type ChangePromptInput,
} from "@repowise-dev/ui/health";
import { RowPromptAction } from "@repowise-dev/ui/health/row-prompt-action";

/**
 * A change prompt: the shared button and dialog in their own client boundary,
 * so the page around them stays server-rendered. The outlined button is the
 * page's one primary action; `quiet` is the per-file row action, revealed on
 * row hover or focus like every other row prompt.
 */
export function ChangePromptAction({
  change,
  quiet = false,
  subject,
}: {
  change: ChangePromptInput;
  quiet?: boolean;
  /** What a quiet row action is about (its file), for its accessible name. */
  subject?: string;
}) {
  // Fixing what got worse when the change made anything worse; else a review
  // of what its checklist flagged. The builder picks the same way.
  const fixing = changePromptFixes(change);
  const [open, setOpen] = useState(false);
  const t = useTranslations("views.changes");
  const label = quiet
    ? t("promptActionFile", { file: subject ?? "" })
    : t(fixing ? "promptActionFix" : "promptAction");
  return (
    <>
      {quiet ? (
        <RowPromptAction variant="icon" label={label} onClick={() => setOpen(true)} />
      ) : (
        <AiPromptButton label={label} onClick={() => setOpen(true)} />
      )}
      <AiPromptModal
        open={open}
        onOpenChange={setOpen}
        getPrompt={(flavor) => buildChangeAiPrompt({ change, flavor })}
        filePath={quiet ? subject : change.label}
        title={t("promptTitle")}
        description={t("promptDescription")}
      />
    </>
  );
}
