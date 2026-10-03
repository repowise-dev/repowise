/**
 * Engine events to session actions. Pure.
 */

import type { Savings } from "@repowise-dev/api-client/costs";
import type { MarginNote, SavingsDelta, SessionAction } from "./session";

/** A finished turn counts only for the main loop; a subagent's carries `agentId`. */
export function fromTurnComplete(e: { agentId?: string | undefined }): SessionAction | null {
  return e.agentId === undefined ? { type: "turnCompleted" } : null;
}

/** The tools whose file Lens follows. */
export const FILE_TOOLS: ReadonlySet<string> = new Set(["Read", "Edit", "Write"]);

const slashes = (p: string) => p.replace(/\\/g, "/").replace(/\/+$/, "");

/**
 * The repo-relative path a file tool works on, or null when it is not a file
 * tool or the file is outside the repo. Windows paths compare without case.
 */
export function fileTarget(e: { tool: string; file_path?: unknown }, repoRoot: string | null): string | null {
  if (!FILE_TOOLS.has(e.tool) || repoRoot === null || typeof e.file_path !== "string") return null;
  const root = slashes(repoRoot);
  const file = slashes(e.file_path);
  const windows = /^[A-Za-z]:\//.test(root) || root.startsWith("//");
  const fold = (p: string) => (windows ? p.toLowerCase() : p);
  if (!fold(file).startsWith(`${fold(root)}/`)) return null;
  return file.slice(root.length + 1);
}

/** The tools whose augment notices become margin notes. */
export const EDIT_TOOLS: ReadonlySet<string> = new Set(["Edit", "Write"]);

// The two edit-time notices `repowise-augment` writes (augment_cmd/decision_inject.py).
const STANDING = /^\[repowise\] .+? is governed by a standing decision: (.+?)(?: because .*)?\.$/;
const MINED = /^\[repowise\] .+? has a decision recorded in it, mined but not reviewed: (.+?)(?: because .*)?\.$/;
const FIXES = /^\[repowise\] .+? has been bug-fixed (\d+)x in the last 6 months, last (.+?)(?: \(bug magnet\))?(?:; mostly in (.+?))?\.$/;

/** Each "(confirmed across N sessions)" tail is the hook's; the title is what Lens shows. */
function stripConfirmed(line: string): string {
  return line.replace(/ \(confirmed across \d+ sessions\)\.$/, ".");
}

/**
 * The margin notes in what the settings hooks added for one tool call
 * (`classic.PostToolUse`'s `additionalContext`, one entry per hook). Only
 * the hook's own edit-time notices count; at most one of each kind.
 */
export function notesFromAugment(additionalContext: unknown): MarginNote[] {
  if (!Array.isArray(additionalContext)) return [];
  const notes: MarginNote[] = [];
  const lines = additionalContext.filter((c): c is string => typeof c === "string").flatMap((c) => c.split(/\r?\n/));
  for (const raw of lines) {
    const line = stripConfirmed(raw.trim());
    const standing = STANDING.exec(line);
    const decision = standing ?? MINED.exec(line);
    const fixes = FIXES.exec(line);
    if (decision !== null && !notes.some((n) => n.kind === "decision")) {
      notes.push({ kind: "decision", reviewed: standing !== null, title: decision[1]! });
    } else if (fixes !== null && !notes.some((n) => n.kind === "fixes")) {
      notes.push({ kind: "fixes", count: Number(fixes[1]), age: fixes[2]!, symbol: fixes[3] ?? null });
    }
  }
  return notes;
}

/**
 * The ledger totals Lens compares: the server's own figures, read as they
 * are. Dollars are the input side only, to match the input-token count.
 */
export function savingsTotals(s: Savings): SavingsDelta {
  return {
    tokens: s.saved_input_tokens,
    inferredTokens: Math.min(s.inferred_saved_input_tokens, s.saved_input_tokens),
    usd: s.priced_input_savings_usd,
  };
}

/**
 * Growth since the snapshot, or null when any figure went down: the ledger
 * only grows, so a drop means it was rebuilt and the snapshot no longer holds.
 */
export function savingsSince(now: SavingsDelta, base: SavingsDelta): SavingsDelta | null {
  const tokens = now.tokens - base.tokens;
  const inferredTokens = now.inferredTokens - base.inferredTokens;
  const usd = now.usd - base.usd;
  if (tokens < 0 || inferredTokens < 0 || usd < 0) return null;
  return { tokens, inferredTokens: Math.min(inferredTokens, tokens), usd };
}
