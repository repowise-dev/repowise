import type { SavingsJobId } from "@repowise-dev/ui/savings";
import type { SetupResponse } from "@/lib/api/setup";
import { setupHref } from "@/components/settings/settings-groups";

/** The setup suggestions the savings page can make, in priority order. Ids
 *  match the Setup rows whose prompt the nudge hands over. */
export type SavingsNudgeId = "rewrite" | "model";

/** The Setup page rule each suggestion links to; the rule id is its anchor. */
const SETUP_ANCHOR: Record<SavingsNudgeId, string> = {
  rewrite: "distill_suggested",
  model: "docs_model_ready",
};

export function nudgeHref(repoId: string, id: SavingsNudgeId): string {
  return `${setupHref(repoId)}#${SETUP_ANCHOR[id]}`;
}

/** Agent activity before a suggestion is worth showing: enough calls that the
 *  savings above it are real, never a calendar date. */
export const NUDGE_MIN_CALLS = 20;

/**
 * Which one setup suggestion to show, if any. All gating lives here so it can
 * move onto server-side setup counters without touching the components.
 *
 * Distill first (it is usually the biggest source), then AI docs. A row whose
 * state is unknown or whose section could not be read never suggests anything,
 * and the savings page never writes setup state.
 * Follow-up: switch to setup.agent_calls, setup.thresholds, setup.unlocks.distill,
 * setup.dismissed and read_hooks once SetupResponse carries them.
 */
export function pickSavingsNudge(
  setup: SetupResponse | undefined,
  agentCalls: number,
  dismissed: ReadonlySet<SavingsNudgeId>,
): SavingsNudgeId | null {
  if (!setup || agentCalls < NUDGE_MIN_CALLS) return null;
  if (setup.rewrite_hook?.state === "not_installed" && !dismissed.has("rewrite")) return "rewrite";
  if (setup.model_available && setup.model?.docs_mode === "deterministic" && !dismissed.has("model")) {
    return "model";
  }
  return null;
}

/** Savings jobs whose source is off, shown muted as "Not set up". */
export function jobsNotSetUp(setup: SetupResponse | undefined): SavingsJobId[] {
  return setup?.rewrite_hook?.state === "not_installed" ? ["trim"] : [];
}

/** Per-viewer, per-repo, per-suggestion. Hides it on this page only. */
export function nudgeDismissKey(repoId: string, id: SavingsNudgeId): string {
  return `repowise:savings-nudge-dismissed:${repoId}:${id}`;
}

export function readDismissed(repoId: string): Set<SavingsNudgeId> {
  const out = new Set<SavingsNudgeId>();
  for (const id of ["rewrite", "model"] as const) {
    try {
      if (window.localStorage.getItem(nudgeDismissKey(repoId, id))) out.add(id);
    } catch {
      // Storage unavailable: the suggestion shows, which is the safe default.
    }
  }
  return out;
}

export function rememberDismissed(repoId: string, id: SavingsNudgeId): void {
  try {
    window.localStorage.setItem(nudgeDismissKey(repoId, id), "1");
  } catch {
    // Not remembered; hidden for this visit only.
  }
}
