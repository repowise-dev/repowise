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

/**
 * Reveal rule for a per-row prompt: hidden at rest, shown on row hover, focus
 * within the row, and where there is no hover. Rows carry `group/row`.
 */
// Follow-up: replace the quiet branch with L-nav's RowPromptAction (ghost
// variant) and the modal with L-perf's useAiPromptAction once both merge.
const ROW_REVEAL =
  "opacity-0 focus-visible:opacity-100 group-hover/row:opacity-100 group-focus-within/row:opacity-100 [@media(hover:none)]:opacity-100";

/**
 * A change prompt: the shared button and dialog in their own client boundary,
 * so the page around them stays server-rendered. The filled button is the
 * page's one primary action; `quiet` is the per-file row action.
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
      <AiPromptButton
        label={label}
        variant={quiet ? "icon" : "default"}
        className={quiet ? ROW_REVEAL : undefined}
        onClick={() => setOpen(true)}
      />
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
