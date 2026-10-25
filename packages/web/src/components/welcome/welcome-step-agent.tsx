"use client";

import { useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { toast } from "sonner";
import { Button } from "@repowise-dev/ui/ui/button";
import { AiPromptButton } from "@repowise-dev/ui/health/ai-prompt-button";
import { CommandLine } from "@repowise-dev/ui/shared/command-line";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { connectSetupAgent, type SetupResponse } from "@/lib/api/setup";
import { MOMENT_MICRO } from "@/components/layout/moment-shell";

type SetupAgent = NonNullable<SetupResponse["agents"]>[number];

const ROW = "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-t border-[var(--color-border-default)] py-2.5 max-[560px]:grid-cols-1";

/** The first config file `repowise agents add` writes for this agent, if known. */
function firstConfigFile(agent: SetupAgent): string | null {
  for (const files of Object.values(agent.config_files ?? {})) {
    if (files[0]) return files[0];
  }
  return null;
}

/** Copies `text`; false when the clipboard is blocked. */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** Step 2: the agents found on this machine, and two questions to try. */
export function WelcomeStepAgent({
  repoId,
  setup,
  hotspotPath,
}: {
  repoId: string;
  setup: SetupResponse | null;
  hotspotPath: string | null;
}) {
  const t = useTranslations("welcome.agent");
  // Connected here, before the setup data catches up.
  const [connected, setConnected] = useState<Record<string, boolean>>({});
  const agents = (setup?.agents ?? []).filter((a) => a.installed || a.connected);
  const questions = [
    t("askRisk"),
    hotspotPath ? t("askHotspot", { path: hotspotPath }) : t("askStart"),
  ];

  return (
    <div>
      {agents.length === 0 ? (
        <p className="border-t border-[var(--color-border-default)] py-2.5 text-xs text-[var(--color-text-secondary)]">
          {t("none")}
        </p>
      ) : (
        agents.map((agent) => (
          <AgentRow
            key={agent.id}
            repoId={repoId}
            agent={agent}
            connected={connected[agent.id] ?? agent.connected}
            writesAllowed={setup?.writes_allowed ?? false}
            callsKnown={setup?.calls_available ?? false}
            onConnected={(value) => setConnected((c) => ({ ...c, [agent.id]: value }))}
          />
        ))
      )}
      <h3 className={`${MOMENT_MICRO} mt-4`}>{t("askLabel")}</h3>
      <ul className="mt-2 flex flex-col gap-1.5">
        {questions.map((q) => (
          <li
            key={q}
            className="flex items-center justify-between gap-3 rounded-md border border-[var(--color-border-default)] bg-[var(--color-release-card)] px-2.5 py-2 text-[13px] text-[var(--color-text-primary)]"
          >
            <span className="min-w-0 [overflow-wrap:anywhere]">{q}</span>
            <CopyQuestion text={q} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function AgentRow({
  repoId,
  agent,
  connected,
  writesAllowed,
  callsKnown,
  onConnected,
}: {
  repoId: string;
  agent: SetupAgent;
  connected: boolean;
  writesAllowed: boolean;
  /** False when the local call log could not be read: say nothing about calls. */
  callsKnown: boolean;
  onConnected: (value: boolean) => void;
}) {
  const t = useTranslations("welcome.agent");
  const format = useFormatter();
  const [busy, setBusy] = useState(false);
  const name = agent.display_name;
  const command = `repowise agents add --target=${agent.id}`;

  async function connect() {
    setBusy(true);
    onConnected(true);
    try {
      const res = await connectSetupAgent(repoId, agent.id);
      if (!res.ok) {
        onConnected(false);
        toast.error(t("connectFailed", { name }), res.message ? { description: res.message } : undefined);
      }
    } catch (e) {
      onConnected(false);
      toast.error(t("connectFailed", { name }), { description: toFriendlyMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  const file = firstConfigFile(agent);
  return (
    <div className={ROW}>
      <div className="min-w-0">
        <h4 className="flex items-center gap-2 text-[13px] font-semibold text-[var(--color-text-primary)]">
          <span
            aria-hidden
            className="h-[7px] w-[7px] shrink-0 rounded-full"
            style={{ background: connected ? "var(--color-success)" : "var(--color-text-tertiary)" }}
          />
          {name}
        </h4>
        <p className="mt-0.5 text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
          {connected
            ? t("connected")
            : file
              ? t.rich("installed", { file, mono: (c) => <span className="font-mono">{c}</span> })
              : t("installedNoFile")}
        </p>
        {!connected && !writesAllowed && <CommandLine command={command} />}
      </div>
      {connected ? (
        callsKnown && (
          <span className="text-xs text-[var(--color-text-tertiary)]">
            {agent.first_call_at
              ? t("firstCall", { when: format.relativeTime(new Date(agent.first_call_at)) })
              : t("waiting")}
          </span>
        )
      ) : (
        <span className="flex flex-wrap items-center justify-end gap-1.5 max-[560px]:justify-start">
          <AiPromptButton
            label={t("copyPrompt")}
            copiedLabel={t("copied")}
            ariaLabel={`${t("copyPrompt")}: ${name}`}
            copyText={() => t("prompt", { name, command })}
          />
          {writesAllowed && (
            <Button variant="outline" size="sm" disabled={busy} onClick={() => void connect()}>
              {busy ? t("connecting") : t("connect")}
            </Button>
          )}
        </span>
      )}
    </div>
  );
}

function CopyQuestion({ text }: { text: string }) {
  const t = useTranslations("welcome.agent");
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        if (!(await copyText(text))) return;
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
      }}
      aria-label={copied ? t("copied") : t("copyQuestion", { question: text })}
      className="shrink-0 rounded text-xs font-medium text-[var(--color-text-primary)] underline decoration-[var(--color-border-hover)] underline-offset-[3px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
    >
      {copied ? t("copied") : t("copy")}
    </button>
  );
}
