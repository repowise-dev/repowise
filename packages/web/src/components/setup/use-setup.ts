"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { toast } from "sonner";
import { useFormatter, useTranslations } from "next-intl";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import {
  addSetupCoverage,
  connectSetupAgent,
  disconnectSetupAgent,
  getOnboardingState,
  getRepoSetup,
  setSetupHook,
  updateOnboardingState,
  type OnboardingPatch,
  type SetupResponse,
  type SetupWriteResponse,
} from "@/lib/api/setup";
import { deriveSetup, type SetupHook, type SetupView } from "./setup-rows";

/** The SWR key every Setup surface shares, so one doctor run feeds them all. */
export const setupKey = (repoId: string) => ["setup", repoId] as const;
export const ONBOARDING_KEY = "setup-onboarding";

/** First load runs the quick doctor (a few seconds), so nothing here refetches on its own. */
const QUIET = {
  revalidateOnFocus: false,
  revalidateOnReconnect: false,
  revalidateIfStale: false,
  dedupingInterval: 10 * 60_000,
  shouldRetryOnError: false,
} as const;

export function useSetupData(repoId: string | null) {
  return useSWR<SetupResponse>(repoId ? setupKey(repoId) : null, () => getRepoSetup(repoId!), QUIET);
}

export function useOnboardingState(enabled = true) {
  return useSWR(enabled ? ONBOARDING_KEY : null, getOnboardingState, QUIET);
}

// -- essentials done, remembered ----------------------------------------------

const DONE_KEY = (repoId: string) => `repowise:setup-done:${repoId}`;

/**
 * Once the essentials are done the sidebar pill stops asking, so a page load
 * does not run doctor just to hide it. The Setup page itself always checks
 * and clears this when something comes undone.
 */
export function rememberSetupDone(repoId: string, done: boolean) {
  try {
    if (done) window.localStorage.setItem(DONE_KEY(repoId), "1");
    else window.localStorage.removeItem(DONE_KEY(repoId));
  } catch {
    /* storage blocked: the pill checks every session */
  }
}

export function setupRememberedDone(repoId: string): boolean {
  try {
    return window.localStorage.getItem(DONE_KEY(repoId)) === "1";
  } catch {
    return false;
  }
}

// -- agents first seen connected ---------------------------------------------

const SEEN_KEY = (repoId: string) => `repowise:setup-agent-seen:${repoId}`;

function readSeen(repoId: string): Record<string, string> | null {
  try {
    const raw = window.localStorage.getItem(SEEN_KEY(repoId));
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, string>) : {};
  } catch {
    return null;
  }
}

/**
 * When this browser first saw each agent connected; feeds the 24h no-call
 * rule. On the first visit for a repo, agents already connected are dated
 * from the index (init registers them then); one connected later is dated
 * from when it was first seen here.
 */
function useAgentSeenAt(repoId: string, setup: SetupResponse | undefined) {
  // null until read from storage, so the 24h rule never runs on a guess.
  const [seen, setSeen] = useState<Record<string, string> | null>(null);
  useEffect(() => {
    if (!setup) return;
    const known = readSeen(repoId);
    const since = known === null ? setup.index.indexed_at : null;
    const next = { ...(known ?? {}) };
    for (const a of setup.agents ?? []) {
      if (a.connected && !next[a.id]) next[a.id] = since ?? new Date().toISOString();
    }
    try {
      window.localStorage.setItem(SEEN_KEY(repoId), JSON.stringify(next));
    } catch {
      /* storage blocked: the rule falls back to the index time */
    }
    setSeen(next);
  }, [repoId, setup]);
  return seen;
}

/** The rows for a repo, in the reader's locale. */
export function useSetupView(repoId: string, repoName: string, setup: SetupResponse | undefined) {
  const format = useFormatter();
  const seen = useAgentSeenAt(repoId, setup);
  return useMemo<SetupView | null>(() => {
    if (!setup || !seen) return null;
    const now = Date.now();
    return deriveSetup(setup, {
      repoName,
      now,
      relative: (iso) => format.relativeTime(new Date(iso), now),
      agentSeenAt: seen,
    });
  }, [setup, repoName, format, seen]);
}

// -- writes ------------------------------------------------------------------

type Patch = (s: SetupResponse) => SetupResponse;

const patchHook =
  (hook: SetupHook, enabled: boolean): Patch =>
  (s) =>
    hook === "post-commit"
      ? { ...s, sync: { ...s.sync, installed: enabled } }
      : { ...s, rewrite_hook: { ...s.rewrite_hook, state: enabled ? "installed" : "not_installed" } };

const patchAgent =
  (agentId: string, connected: boolean): Patch =>
  (s) => ({
    ...s,
    agents: (s.agents ?? []).map((a) => (a.id === agentId ? { ...a, connected } : a)),
  });

const patchCoverage: Patch = (s) => ({
  ...s,
  coverage: { ...s.coverage, ingested_at: new Date().toISOString() },
});

/**
 * Setup writes. Each shows its result on the control first, then rolls the
 * control back when the command reports `ok: false` (or the request fails)
 * and says why. Success refetches, since a write can change other rows.
 */
export function useSetupWrites(repoId: string) {
  const t = useTranslations("setup");
  const { data, mutate } = useSetupData(repoId);
  const [busy, setBusy] = useState<string | null>(null);

  const run = useCallback(
    async (id: string, patch: Patch, call: () => Promise<SetupWriteResponse>) => {
      const before = data;
      setBusy(id);
      if (before) await mutate(patch(before), { revalidate: false });
      try {
        const res = await call();
        if (!res.ok) {
          await mutate(before, { revalidate: false });
          toast.error(res.message || t("writeFailed"), { description: res.command });
          return false;
        }
        if (res.message) toast.success(res.message);
        void mutate();
        return true;
      } catch (e) {
        await mutate(before, { revalidate: false });
        toast.error(t("writeFailed"), { description: toFriendlyMessage(e) });
        return false;
      } finally {
        setBusy(null);
      }
    },
    [data, mutate, t],
  );

  return {
    busy,
    setHook: (hook: SetupHook, enabled: boolean) =>
      run(`hook:${hook}`, patchHook(hook, enabled), () => setSetupHook(repoId, hook, enabled)),
    connect: (agentId: string) =>
      run(`agent:${agentId}`, patchAgent(agentId, true), () => connectSetupAgent(repoId, agentId)),
    disconnect: (agentId: string) =>
      run(`agent:${agentId}`, patchAgent(agentId, false), () => disconnectSetupAgent(repoId, agentId)),
    addCoverage: (path: string) =>
      run("coverage", patchCoverage, () => addSetupCoverage(repoId, path)),
  };
}

/**
 * Hide an onboarding surface on this machine. The server keeps it in
 * `~/.repowise/platform.json`; when it refuses (a server with an API key),
 * the surface still hides for this session.
 */
export function useHideOnThisMachine() {
  const { mutate } = useSWRConfig();
  const [hiddenNow, setHiddenNow] = useState<Set<keyof OnboardingPatch>>(new Set());
  const hide = useCallback(
    async (field: "setup_pill_hidden" | "hosted_card_hidden") => {
      setHiddenNow((prev) => new Set(prev).add(field));
      try {
        const next = await updateOnboardingState({ [field]: true });
        await mutate(ONBOARDING_KEY, next, { revalidate: false });
      } catch {
        /* refused or offline: hidden for this session only */
      }
    },
    [mutate],
  );
  return { hide, hiddenNow };
}
