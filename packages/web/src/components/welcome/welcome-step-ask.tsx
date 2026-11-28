"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { Switch } from "@repowise-dev/ui/ui/switch";
import { ConfirmDialog } from "@repowise-dev/ui/ui/confirm-dialog";
import { CommandLine } from "@repowise-dev/ui/shared/command-line";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { firstPrompts, type FirstPromptFacts } from "@repowise-dev/ui/onboarding/first-prompts";
import { setSetupHook, type SetupResponse } from "@/lib/api/setup";
import { hostedLink } from "@/lib/hosted";
import { MOMENT_BODY, MOMENT_CAPTION, MOMENT_CARD, MOMENT_LINK } from "@/components/layout/moment-shell";
import { CopyQuestion } from "./welcome-step-agent";

const ROW = "flex items-center justify-between gap-4 py-4 last:pb-0";
const ROW_TITLE = `${MOMENT_BODY} font-medium text-[var(--color-text-primary)]`;

/** Whether to offer hosted: tips on, and not already signed in. */
export function showsHostedRow(setup: SetupResponse | null): boolean {
  return !!setup && setup.onboarding.hints_enabled && !setup.onboarding.signed_in;
}

/** Step 3: the first questions to ask, built from this repo's index, then
 *  sync and hosted for a team. A model is asked for where it matters (Docs,
 *  Chat), not here. */
export function WelcomeStepAsk({
  repoId,
  repoName,
  setup,
  promptFacts,
}: {
  repoId: string;
  repoName: string;
  setup: SetupResponse | null;
  promptFacts: FirstPromptFacts;
}) {
  const t = useTranslations("welcome.ask");
  const questions = firstPrompts(repoName, promptFacts);
  return (
    <div className="space-y-6">
      <ul className={`${MOMENT_CARD} divide-y divide-[var(--color-border-default)]`}>
        {questions.map((q) => (
          <li key={q} className="flex items-center justify-between gap-4 py-1 pl-4 pr-2">
            <span className={`${MOMENT_BODY} min-w-0 py-2 text-[var(--color-text-primary)] [overflow-wrap:anywhere]`}>
              {q}
            </span>
            <CopyQuestion text={q} />
          </li>
        ))}
      </ul>
      <div>
        <ul className="divide-y divide-[var(--color-border-default)]">
          <SyncRow repoId={repoId} setup={setup} />
          {showsHostedRow(setup) && (
            <li className={ROW}>
              <div className="min-w-0">
                <h4 className={ROW_TITLE}>{t("teamTitle")}</h4>
                <p className={MOMENT_CAPTION}>{t("teamBody")}</p>
              </div>
              <a
                href={hostedLink("welcome", "team")}
                target="_blank"
                rel="noreferrer"
                className={`${MOMENT_LINK} shrink-0`}
              >
                {t("teamLink")}
              </a>
            </li>
          )}
        </ul>
        <p className={`${MOMENT_CAPTION} mt-4`}>{t("later")}</p>
      </div>
    </div>
  );
}

function SyncRow({ repoId, setup }: { repoId: string; setup: SetupResponse | null }) {
  const t = useTranslations("welcome.ask");
  const tc = useTranslations("common");
  // Local until the setup data catches up with a change made here.
  const [on, setOn] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmOff, setConfirmOff] = useState(false);
  const installed = on ?? setup?.sync.installed ?? false;
  const writesAllowed = setup?.writes_allowed ?? false;

  async function apply(enabled: boolean) {
    setBusy(true);
    setOn(enabled);
    try {
      const res = await setSetupHook(repoId, "post-commit", enabled);
      if (!res.ok) {
        setOn(!enabled);
        toast.error(t("syncFailed"), res.message ? { description: res.message } : undefined);
      }
    } catch (e) {
      setOn(!enabled);
      toast.error(t("syncFailed"), { description: toFriendlyMessage(e) });
    } finally {
      setBusy(false);
      setConfirmOff(false);
    }
  }

  return (
    <li className={ROW}>
      <div className="min-w-0">
        <h4 id="welcome-sync" className={ROW_TITLE}>
          {t("syncTitle")}
        </h4>
        <p className={MOMENT_CAPTION}>{t("syncBody")}</p>
        {!writesAllowed && !installed && (
          <div className="mt-2">
            <CommandLine command="repowise hook install" />
          </div>
        )}
      </div>
      {/* The unchecked track is drawn in the border tone so it reads on the
          moment ground. */}
      <Switch
        aria-labelledby="welcome-sync"
        checked={installed}
        disabled={!writesAllowed || busy}
        onCheckedChange={(next) => (next ? void apply(true) : setConfirmOff(true))}
        className="data-[state=unchecked]:bg-[var(--color-border-hover)]"
      />
      <ConfirmDialog
        open={confirmOff}
        onOpenChange={setConfirmOff}
        title={t("syncOffTitle")}
        description={t("syncOffBody")}
        confirmLabel={t("syncOffConfirm")}
        cancelLabel={tc("cancel")}
        loading={busy}
        onConfirm={() => apply(false)}
      />
    </li>
  );
}
