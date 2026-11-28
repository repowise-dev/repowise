"use client";

import useSWR from "swr";
import type { ActionRule, ActionsResponse, NextAction } from "@repowise-dev/types/actions";
import { getActionPrompt, getActions } from "@/lib/api/actions";
import { useEvidenceConnected } from "./use-setup";

/** The same key the overview's Do next seeds, so one read serves both. */
export const actionsKey = (repoId: string) => `actions:${repoId}`;

/** The repo's next actions, setup steps included. Read once per page; a null
 *  repo reads nothing. */
export function useRepoActions(repoId: string | null) {
  return useSWR<ActionsResponse>(repoId ? actionsKey(repoId) : null, () => getActions(repoId!), {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  });
}

/** The visible action a rule raised, in either horizon; null when it raised none. */
export function findAction(
  view: Pick<ActionsResponse, "horizons"> | null | undefined,
  rule: ActionRule,
): NextAction | null {
  for (const horizon of Object.values(view?.horizons ?? {})) {
    const hit = horizon.actions.find((a) => a.rule === rule);
    if (hit) return hit;
  }
  return null;
}

/**
 * Whether runtime evidence is stored for the repo, and the profile or
 * benchmark files the `evidence_found` step names. Null until both reads
 * settle, or when the evidence status cannot be read, so a view stays silent
 * instead of guessing.
 */
export function useRuntimeEvidence(
  repoId: string,
): { connected: boolean; found: string[] } | null {
  const connected = useEvidenceConnected(repoId);
  const { data: actions, error } = useRepoActions(repoId);
  if (connected === null) return null;
  if (!actions && !error) return null;
  const step = findAction(actions, "evidence_found");
  return { connected, found: step?.details.map((d) => d.path) ?? [] };
}

/**
 * A model source found on this machine that could write the docs (the
 * `docs_model_ready` step: a key or a local model), and that step's prompt,
 * read from the server so it is the same text `repowise next` prints. Null
 * while there is none, so a surface shows its plain keyless state.
 */
export function useModelReady(repoId: string | null): { source: string; prompt: string | null } | null {
  const { data } = useRepoActions(repoId);
  const step = repoId ? findAction(data, "docs_model_ready") : null;
  const { data: prompt } = useSWR(
    step ? ["action-prompt", repoId, step.id] : null,
    async () => (await getActionPrompt(repoId!, step!.id)).text,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );
  const source = step?.why.find((w) => w.label === "model found")?.value;
  return source ? { source, prompt: prompt ?? null } : null;
}
