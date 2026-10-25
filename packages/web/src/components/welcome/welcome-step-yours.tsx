"use client";

import { useState } from "react";
import useSWR from "swr";
import { useTranslations } from "next-intl";
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
import { MOMENT_MICRO } from "@/components/layout/moment-shell";

const ROW = "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-t border-[var(--color-border-default)] py-2.5";
const DOT = "h-[7px] w-[7px] shrink-0 rounded-full";

/** Whether to offer hosted: tips on, and not already signed in. */
export function showsHostedRow(setup: SetupResponse | null): boolean {
  return !!setup && setup.onboarding.hints_enabled && !setup.onboarding.signed_in;
}

/** Step 3: a model, sync, and hosted for a team. Every part is optional. */
export function WelcomeStepYours({ repoId, setup }: { repoId: string; setup: SetupResponse | null }) {
  const t = useTranslations("welcome.yours");
  return (
    <div>
      {setup?.model.docs_mode !== "llm" && <ModelBox repoId={repoId} />}
      <SyncRow repoId={repoId} setup={setup} />
      {showsHostedRow(setup) && (
        <div className={ROW}>
          <div className="min-w-0">
            <h4 className="flex items-center gap-2 text-[13px] font-semibold text-[var(--color-text-primary)]">
              <span aria-hidden className={DOT} style={{ background: "var(--color-info)" }} />
              {t("teamTitle")}
            </h4>
            <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">{t("teamBody")}</p>
          </div>
          <a
            href={hostedLink("welcome", "team")}
            target="_blank"
            rel="noreferrer"
            className="rounded text-xs font-medium text-[var(--color-text-primary)] underline decoration-[var(--color-border-hover)] underline-offset-[3px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          >
            {t("teamLink")}
          </a>
        </div>
      )}
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

function ModelBox({ repoId }: { repoId: string }) {
  const t = useTranslations("welcome.yours");
  const tm = useTranslations("modelUpgrade");
  const { openSheet, sheet } = useModelUpgrade(repoId);
  const detected = useDetectedSources(repoId);
  const sourceName = (s: ModelSource) =>
    s.kind === "cli" ? tm("cliSource", { name: s.name }) : s.kind === "local" ? tm("localSource", { name: s.name }) : tm("keySource", { name: s.name });
  const gets = [t("gets.docs"), t("gets.chat"), t("gets.answers"), t("gets.local")];

  return (
    <section
      aria-labelledby="welcome-model"
      className="mb-3 rounded-[10px] border border-[color-mix(in_srgb,var(--color-model)_30%,transparent)] bg-[var(--color-model-muted)] p-3.5"
    >
      <div className="flex items-baseline justify-between gap-2.5">
        <h4 id="welcome-model" className="text-[13.5px] font-semibold text-[var(--color-text-primary)]">
          {t("modelTitle")}
        </h4>
        <span className={MOMENT_MICRO}>{t("optional")}</span>
      </div>
      <ul className="my-2.5 grid grid-cols-1 gap-x-3.5 gap-y-1.5 text-xs text-[var(--color-text-secondary)] min-[561px]:grid-cols-2">
        {gets.map((g) => (
          <li key={g} className="flex items-baseline gap-2">
            <span aria-hidden className={`${DOT} translate-y-[-1px]`} style={{ background: "var(--color-model)" }} />
            {g}
          </li>
        ))}
      </ul>
      {detected.length > 0 && (
        <div className="mb-3">
          <p className={`${MOMENT_MICRO} mb-1`}>{t("detectedLabel")}</p>
          <ul className="flex flex-col gap-1">
            {detected.map((s) => (
              <li
                key={s.id}
                className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-release-card)] px-2.5 py-1.5 text-[12.5px] text-[var(--color-text-primary)]"
              >
                {sourceName(s)}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex justify-end">
        <Button
          size="sm"
          onClick={openSheet}
          className="h-8 bg-[var(--color-model)] text-[var(--color-text-on-model)] hover:bg-[var(--color-model-hover)] focus-visible:ring-[var(--color-model)] max-[560px]:w-full"
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
    <div className={ROW}>
      <div className="min-w-0">
        <h4 id="welcome-sync" className="text-[13px] font-semibold text-[var(--color-text-primary)]">
          {t("syncTitle")}
        </h4>
        <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">{t("syncBody")}</p>
        {!writesAllowed && !installed && <CommandLine command="repowise hook install" />}
      </div>
      <Switch
        aria-labelledby="welcome-sync"
        checked={installed}
        disabled={!writesAllowed || busy}
        onCheckedChange={(next) => (next ? void apply(true) : setConfirmOff(true))}
        className="data-[state=checked]:bg-[var(--color-success)]"
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
    </div>
  );
}
