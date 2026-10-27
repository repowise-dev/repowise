"use client";

import { useState } from "react";
import useSWR from "swr";
import { useFormatter, useTranslations } from "next-intl";
import { toast } from "sonner";
import { Button } from "@repowise-dev/ui/ui/button";
import { Switch } from "@repowise-dev/ui/ui/switch";
import { ConfirmDialog } from "@repowise-dev/ui/ui/confirm-dialog";
import { CommandLine } from "@repowise-dev/ui/shared/command-line";
import { orderModelSources, type ModelSource } from "@repowise-dev/ui/settings";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { setSetupHook, type SetupResponse } from "@/lib/api/setup";
import { getProviders } from "@/lib/api/providers";
import { hostedLink } from "@/lib/hosted";
import { useModelUpgrade } from "@/components/model-upgrade/model-upgrade";
import { MOMENT_BODY, MOMENT_CAPTION, MOMENT_LINK } from "@/components/layout/moment-shell";

const ROW = "flex items-center justify-between gap-4 py-4 last:pb-0";
const ROW_TITLE = `${MOMENT_BODY} font-medium text-[var(--color-text-primary)]`;

/** Whether to offer hosted: tips on, and not already signed in. */
export function showsHostedRow(setup: SetupResponse | null): boolean {
  return !!setup && setup.onboarding.hints_enabled && !setup.onboarding.signed_in;
}

/** Step 3: a model, sync, and hosted for a team. Every part is optional. */
export function WelcomeStepYours({ repoId, setup }: { repoId: string; setup: SetupResponse | null }) {
  const t = useTranslations("welcome.yours");
  return (
    <div className="space-y-4">
      {setup && setup.model.docs_mode !== "llm" && <ModelBox repoId={repoId} />}
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
    </div>
  );
}

/** Sources found on this machine. Shares the sheet's cache key, so opening
 *  the sheet afterwards does not ask each agent CLI again. */
function useDetectedSources(repoId: string): ModelSource[] {
  const { data } = useSWR(
    ["/api/providers", repoId, "detect"],
    () => getProviders(repoId, { detect: true }),
    { revalidateOnFocus: false },
  );
  return orderModelSources(data?.providers ?? []).filter((s) => s.detected);
}

/** The model offer: the one plum surface in the welcome. */
function ModelBox({ repoId }: { repoId: string }) {
  const t = useTranslations("welcome.yours");
  const tm = useTranslations("modelUpgrade");
  const format = useFormatter();
  const { openSheet, sheet } = useModelUpgrade(repoId);
  const detected = useDetectedSources(repoId);
  const sourceName = (s: ModelSource) =>
    s.kind === "cli" ? tm("cliSource", { name: s.name }) : s.kind === "local" ? tm("localSource", { name: s.name }) : tm("keySource", { name: s.name });
  const gets = [t("gets.docs"), t("gets.chat"), t("gets.answers"), t("gets.local")];

  return (
    <section
      aria-labelledby="welcome-model"
      className="rounded-lg border border-[color-mix(in_srgb,var(--color-model)_30%,transparent)] bg-[var(--color-model-muted)] p-4"
    >
      <h4 id="welcome-model" className={ROW_TITLE}>
        {t("modelTitle")}
      </h4>
      <ul className="mt-3 grid grid-cols-1 gap-x-4 gap-y-2 text-xs text-[var(--color-text-secondary)] min-[561px]:grid-cols-2">
        {gets.map((g) => (
          <li key={g} className="flex items-center gap-2">
            <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-[var(--color-model)]" />
            {g}
          </li>
        ))}
      </ul>
      <div className="mt-4 flex flex-col gap-3 min-[561px]:flex-row min-[561px]:items-center min-[561px]:justify-between">
        <p className={MOMENT_CAPTION}>
          {detected.length > 0 &&
            t("detected", { names: format.list(detected.map(sourceName), { type: "conjunction" }) })}
        </p>
        <Button
          onClick={openSheet}
          className="h-11 shrink-0 bg-[var(--color-model)] text-[var(--color-text-on-model)] hover:bg-[var(--color-model-hover)] focus-visible:ring-[var(--color-model)] min-[561px]:h-9"
        >
          {tm("write")}
        </Button>
      </div>
      {sheet}
    </section>
  );
}

function SyncRow({ repoId, setup }: { repoId: string; setup: SetupResponse | null }) {
  const t = useTranslations("welcome.yours");
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
