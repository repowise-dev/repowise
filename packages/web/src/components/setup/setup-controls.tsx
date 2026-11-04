"use client";

import { useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@repowise-dev/ui/ui/button";
import { Switch } from "@repowise-dev/ui/ui/switch";
import { ConfirmDialog } from "@repowise-dev/ui/ui/confirm-dialog";
import { useAiPromptAction } from "@repowise-dev/ui/health/ai-prompt-action";
import { buildSetupAiPrompt } from "@repowise-dev/ui/health/ai-prompt-builder";
import { useModelUpgrade } from "@/components/model-upgrade/model-upgrade";
import type { SetupAction, SetupRow } from "./setup-rows";
import { useSetupText } from "./setup-row";
import { useSetupData, useSetupWrites } from "./use-setup";

/** Green when on, the same switch everywhere Setup rows render. */
const SWITCH_ON = "data-[state=checked]:bg-[var(--color-success)]";

/**
 * Everything a Setup row's controls need: the writes, the sync-off confirm,
 * the model sheet and the prompt modal. Render `dialogs` once and call
 * `actionFor(row)` per row. Shared by the Setup page and repo settings.
 */
export function useSetupControls(repoId: string) {
  const t = useTranslations("setup");
  const text = useSetupText();
  const { data, mutate } = useSetupData(repoId);
  const writes = useSetupWrites(repoId);
  const model = useModelUpgrade(repoId, { onGenerated: () => void mutate() });
  const { openPrompt, promptModal } = useAiPromptAction();
  const [confirmSyncOff, setConfirmSyncOff] = useState(false);

  const writesAllowed = data?.writes_allowed ?? false;
  // One write at a time: each rolls back to the payload it started from.
  const writesOffTitle = writesAllowed ? undefined : t("writesOffShort");

  function hookSwitch(row: SetupRow, action: Extract<SetupAction, { kind: "hook" }>) {
    return (
      <Switch
        checked={action.enabled}
        disabled={!writesAllowed || action.unavailable || writes.busy !== null}
        aria-label={text(row.title)}
        title={writesOffTitle}
        className={SWITCH_ON}
        onCheckedChange={(next) => {
          if (action.hook === "post-commit" && !next) setConfirmSyncOff(true);
          else void writes.setHook(action.hook, next);
        }}
      />
    );
  }

  function actionFor(row: SetupRow): ReactNode {
    const action = row.action;
    if (!action) return null;
    switch (action.kind) {
      case "hook":
        return hookSwitch(row, action);
      case "connect":
        return (
          <Button
            variant="outline"
            size="sm"
            disabled={!writesAllowed || writes.busy !== null}
            title={writesOffTitle}
            onClick={() => void writes.connect(action.agentId)}
          >
            {t("connect", { agent: action.agentName })}
          </Button>
        );
      case "disconnect":
        return (
          <Button
            variant="ghost"
            size="sm"
            disabled={!writesAllowed || writes.busy !== null}
            title={writesOffTitle}
            aria-label={t("disconnectAgent", { agent: action.agentName })}
            onClick={() => void writes.disconnect(action.agentId)}
          >
            {t("disconnect")}
          </Button>
        );
      case "coverage":
        return (
          <Button
            variant="outline"
            size="sm"
            disabled={!writesAllowed || writes.busy !== null}
            title={writesOffTitle}
            onClick={() => void writes.addCoverage(action.path)}
          >
            {t("useReport")}
          </Button>
        );
      case "model":
        return (
          <Button
            size="sm"
            className="bg-[var(--color-model)] text-[var(--color-text-on-model)] hover:bg-[var(--color-model-hover)]"
            onClick={model.openSheet}
          >
            {action.hasModel ? t("changeModel") : t("addModel")}
          </Button>
        );
      default:
        return null;
    }
  }

  function openRowPrompt(row: SetupRow) {
    openPrompt({
      title: text(row.title),
      description: t("promptDescription"),
      getPrompt: (flavor) => buildSetupAiPrompt(row.prompt, flavor),
    });
  }

  const dialogs = (
    <>
      <ConfirmDialog
        open={confirmSyncOff}
        onOpenChange={setConfirmSyncOff}
        title={t("syncOff.title")}
        description={t("syncOff.body")}
        confirmLabel={t("syncOff.confirm")}
        cancelLabel={t("syncOff.cancel")}
        destructive
        onConfirm={() => {
          setConfirmSyncOff(false);
          void writes.setHook("post-commit", false);
        }}
      />
      {model.sheet}
      {promptModal}
    </>
  );

  return { actionFor, openRowPrompt, dialogs, writes, writesAllowed };
}
