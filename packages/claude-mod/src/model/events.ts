/**
 * Engine events to session actions. Pure.
 */

import type { Savings } from "@repowise-dev/api-client/costs";
import { normalizeRepoPath } from "@repowise-dev/types/repos";
import type { MarginNote, SavingsDelta, SessionAction } from "./session";
import type { TrailAction } from "./trail";

/** A finished turn counts only for the main loop; a subagent's carries `agentId`. */
export function fromTurnComplete(e: { agentId?: string | undefined }): SessionAction | null {
  return e.agentId === undefined ? { type: "turnCompleted" } : null;
}

/** The tools whose file Lens follows. */
export const FILE_TOOLS: ReadonlySet<string> = new Set(["Read", "Edit", "Write"]);

/**
 * The repo-relative path a file tool works on, or null when it is not a file
 * tool or the file is outside the repo. Windows paths compare without case.
 */
export function fileTarget(e: { tool: string; file_path?: unknown }, repoRoot: string | null): string | null {
  if (!FILE_TOOLS.has(e.tool) || repoRoot === null || typeof e.file_path !== "string") return null;
  const windows = /^[A-Za-z]:[\\/]|^[\\/]{2}/.test(repoRoot);
  return relativeTo(e.file_path, repoRoot, windows);
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

// Where Claude looked, for the map.

/** The tools whose calls say where Claude looked. */
export const OBSERVED_TOOLS: ReadonlySet<string> = new Set(["Read", "Edit", "Write", "Grep", "Glob"]);

/** Lens's own MCP calls fire tool events too; they are not Claude's attention. */
const OWN_CALL_PREFIX = "toolu_plugin_";

export interface PathContext {
  /** The session's working directory, which relative tool paths resolve against. */
  cwd: string;
  isWindows: boolean;
}

/** `raw` resolved against the cwd, with forward slashes on Windows; case kept. */
export function absolutePath(raw: string, ctx: PathContext): string {
  const absolute = ctx.isWindows ? /^[A-Za-z]:[\\/]|^\\\\/.test(raw) : raw.startsWith("/");
  // Ceiling: `..` segments are kept as written; the tools Lens reads report resolved paths.
  const joined = absolute ? raw : `${ctx.cwd.replace(/[\\/]+$/, "")}/${raw.replace(/^\.[\\/]/, "")}`;
  return ctx.isWindows ? joined.replace(/\\/g, "/") : joined;
}

/** Absolute and normalized: forward slashes and, on Windows, lowercase. */
export function absoluteKey(raw: string, ctx: PathContext): string {
  return normalizeRepoPath(absolutePath(raw, ctx), ctx.isWindows);
}

/** `absolute` relative to `root` with its own case kept, or null outside it. */
export function relativeTo(absolute: string, root: string, isWindows: boolean): string | null {
  const rootKey = normalizeRepoPath(root, isWindows);
  const slashed = isWindows ? absolute.replace(/\\/g, "/") : absolute;
  if (!normalizeRepoPath(slashed, isWindows).startsWith(`${rootKey}/`)) return null;
  return slashed.slice(rootKey.length + 1);
}

/** What `next(e)` resolves to for a tool call, as recorded (test/fixtures/tool-calls.json). */
interface ToolOutcome {
  isError?: boolean;
  result?: { mode?: string; filenames?: unknown } | string;
}

function filenamesOf(out: ToolOutcome): string[] {
  const names = typeof out.result === "object" ? out.result.filenames : undefined;
  return Array.isArray(names) ? names.filter((v): v is string => typeof v === "string") : [];
}

/** A Grep that printed lines (`content`) names no files: it says nothing about where the hits are. */
function isContentGrep(tool: string, out: ToolOutcome): boolean {
  return tool === "Grep" && typeof out.result === "object" && out.result.mode === "content";
}

function searchAction(out: ToolOutcome, ctx: PathContext): TrailAction {
  return { type: "search", paths: filenamesOf(out).map((f) => absoluteKey(f, ctx)) };
}

function fileAction(e: { tool: string; [arg: string]: unknown }, ctx: PathContext): TrailAction | null {
  const raw = e["file_path"];
  if (typeof raw !== "string") return null;
  const path = absoluteKey(raw, ctx);
  return e.tool === "Read" ? { type: "read", path } : { type: "edit", path };
}

/**
 * What Claude's tool call says about where it looked, from the call and what
 * `next(e)` returned: a read, a search's hits, or an edit. Subagent calls
 * count (they are Claude's attention too); MCP and other tools do not, and
 * neither do Lens's own calls, a call that failed, or a content-mode Grep.
 */
export function fromToolCall(
  e: { tool: string; tool_use_id?: string | undefined; [arg: string]: unknown },
  outcome: unknown,
  ctx: PathContext,
): TrailAction | null {
  if (!OBSERVED_TOOLS.has(e.tool) || e.tool_use_id?.startsWith(OWN_CALL_PREFIX)) return null;
  const out = (outcome ?? {}) as ToolOutcome;
  if (out.isError === true || isContentGrep(e.tool, out)) return null;
  return e.tool === "Grep" || e.tool === "Glob" ? searchAction(out, ctx) : fileAction(e, ctx);
}

/**
 * The file an edit wrote, as the recap and the brief name it: repo-relative
 * inside the repo (case kept), else absolute; null without a path.
 */
export function touchedPath(
  e: { file_path?: unknown; [arg: string]: unknown },
  cwd: string | null,
  repoRoot: string | null,
): string | null {
  const raw = typeof e.file_path === "string" ? e.file_path : e["notebook_path"];
  if (typeof raw !== "string") return null;
  const base = cwd ?? repoRoot;
  if (base === null) return raw;
  const isWindows = /^[A-Za-z]:[\/]|^[\/]{2}/.test(base);
  const abs = absolutePath(raw, { cwd: base, isWindows });
  return (repoRoot === null ? null : relativeTo(abs, repoRoot, isWindows)) ?? abs;
}
