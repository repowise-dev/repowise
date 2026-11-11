"use client";

import { useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { toast } from "sonner";
import { Button } from "@repowise-dev/ui/ui/button";
import { AiPromptButton } from "@repowise-dev/ui/health/ai-prompt-button";
import { CommandLine } from "@repowise-dev/ui/shared/command-line";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import { connectSetupAgent, type SetupResponse } from "@/lib/api/setup";
import {
  MOMENT_BODY,
  MOMENT_CAPTION,
  MOMENT_CARD,
  MOMENT_LINK,
  MOMENT_MICRO,
} from "@/components/layout/moment-shell";

type SetupAgent = NonNullable<SetupResponse["agents"]>[number];

/** Rows shown before the rest are left to Setup, which lists every agent. */
const AGENTS_SHOWN = 4;

const ROW = "flex flex-col gap-3 py-4 first:pt-0 last:pb-0 min-[561px]:flex-row min-[561px]:items-center min-[561px]:justify-between";

/** The first config file `repowise agents add` writes for this agent, if known. */
function firstConfigFile(agent: SetupAgent): string | null {
  for (const files of Object.values(agent.config_files ?? {})) {
    if (files[0]) return files[0];
  }
  return null;
}

/** The end of a config path: the file, with its folder when that is a dot
 *  folder (`.cursor/mcp.json`). The full path stays in the tooltip. */
export function shortConfigPath(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  const file = parts[parts.length - 1] ?? path;
  const dir = parts[parts.length - 2];
  return dir?.startsWith(".") ? `${dir}/${file}` : file;
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

/** Step 2: the agents found on this machine, connected ones first, and two
 *  questions to try. */
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
  const found = (setup?.agents ?? [])
    .filter((a) => a.installed || a.connected)
    .sort((a, b) => Number(b.connected) - Number(a.connected));
  const agents = found.slice(0, AGENTS_SHOWN);
  const more = found.length - agents.length;
  const questions = [
    t("askRisk"),
    hotspotPath ? t("askHotspot", { path: hotspotPath }) : t("askStart"),
  ];

  return (
    <div className="space-y-8">
      <section aria-labelledby="welcome-agents">
        <h3 id="welcome-agents" className={`${MOMENT_MICRO} mb-3`}>
          {t("listLabel")}
        </h3>
        {agents.length === 0 ? (
          <p className={MOMENT_CAPTION}>{t("none")}</p>
        ) : (
          <ul className="divide-y divide-[var(--color-border-default)]">
            {agents.map((agent) => (
              <AgentRow
                key={agent.id}
                repoId={repoId}
                agent={agent}
                connected={connected[agent.id] ?? agent.connected}
                writesAllowed={setup?.writes_allowed ?? false}
                callsKnown={setup?.calls_available ?? false}
                onConnected={(value) => setConnected((c) => ({ ...c, [agent.id]: value }))}
              />
            ))}
          </ul>
        )}
        {more > 0 && <p className={`${MOMENT_CAPTION} mt-3`}>{t("moreAgents", { count: more })}</p>}
      </section>

      <section aria-labelledby="welcome-ask">
        <h3 id="welcome-ask" className={`${MOMENT_MICRO} mb-3`}>
          {t("askLabel")}
        </h3>
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
      </section>
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
    <li className={ROW}>
      <div className="min-w-0">
        <h4 className={`${MOMENT_BODY} flex items-center gap-2 font-medium text-[var(--color-text-primary)]`}>
          <span
            aria-hidden
            className="size-1.5 shrink-0 rounded-full"
            style={{ background: connected ? "var(--color-success)" : "var(--color-border-hover)" }}
          />
          {name}
        </h4>
        <p className={`${MOMENT_CAPTION} pl-3.5 [overflow-wrap:anywhere]`}>
          {connected
            ? t("connected")
            : file
              ? t.rich("installed", {
                  file: shortConfigPath(file),
                  mono: (c) => (
                    <span className="font-mono" title={file}>
                      {c}
                    </span>
                  ),
                })
              : t("installedNoFile")}
        </p>
        {!connected && !writesAllowed && (
          <div className="mt-2 pl-3.5">
            <CommandLine command={command} />
          </div>
        )}
      </div>
      <RowAction
        connected={connected}
        callsKnown={callsKnown}
        firstCallAt={agent.first_call_at ?? null}
        writesAllowed={writesAllowed}
        busy={busy}
        name={name}
        buildPrompt={() => t("prompt", { name, command })}
        onConnect={() => void connect()}
      />
    </li>
  );
}

/** The right side of an agent row: its call status once connected, or the
 *  two ways to connect it. */
function RowAction({
  connected,
  callsKnown,
  firstCallAt,
  writesAllowed,
  busy,
  name,
  buildPrompt,
  onConnect,
}: {
  connected: boolean;
  callsKnown: boolean;
  firstCallAt: string | null;
  writesAllowed: boolean;
  busy: boolean;
  name: string;
  buildPrompt: () => string;
  onConnect: () => void;
}) {
  const t = useTranslations("welcome.agent");
  const format = useFormatter();
  if (connected) {
    if (!callsKnown) return null;
    return (
      <span className="shrink-0 pl-3.5 text-xs text-[var(--color-text-tertiary)] min-[561px]:pl-0">
        {firstCallAt ? t("firstCall", { when: format.relativeTime(new Date(firstCallAt)) }) : t("waiting")}
      </span>
    );
  }
  return (
    <span className="flex shrink-0 flex-wrap items-center gap-2 pl-3.5 min-[561px]:pl-0">
      <AiPromptButton
        label={t("copyPrompt")}
        copiedLabel={t("copied")}
        failedLabel={t("copyFailed")}
        ariaLabel={`${t("copyPrompt")}: ${name}`}
        copyText={buildPrompt}
        className="max-[560px]:h-11"
      />
      {writesAllowed && (
        <Button variant="outline" size="sm" className="h-11 min-[561px]:h-8" disabled={busy} onClick={onConnect}>
          {busy ? t("connecting") : t("connect")}
        </Button>
      )}
    </span>
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
      className={`${MOMENT_LINK} grid min-h-11 min-w-11 shrink-0 place-items-center px-2`}
    >
      {copied ? t("copied") : t("copy")}
    </button>
  );
}
