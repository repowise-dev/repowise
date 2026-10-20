/**
 * Flow: Claude's tool calls this session as the engine reported them, in
 * order and timed: what each Repowise reply carried, how many bytes each
 * result put in Claude's context, the files it read and edited after a reply
 * named them, and what the index says each edited file reaches. Pure: every
 * time arrives on an action (epoch ms); nothing here reads a clock. The map
 * reads visit order and timing from `visits`.
 */

import { summarizeReply, type ReplySummary } from "./replies";

/** Turns kept whole; older ones fold into `earlier`. */
export const MAX_TURNS = 6;
/** Activities kept per turn; older ones are counted in `dropped`. */
export const MAX_ACTIVITIES = 60;
const PROMPT_CHARS = 160;
/**
 * Paths Repowise named this session, for "found via". Ceiling: past this the
 * newest namings are not kept; raise it if long sessions need more.
 */
const MAX_NAMED = 2_000;
/** Argument values kept per activity, and their length. */
const MAX_ARGS = 4;
const ARG_CHARS = 160;
/** Characters of a shell command kept, to see which tests it ran. */
const COMMAND_CHARS = 2_000;
/** Hit paths kept per search. */
const MAX_HIT_PATHS = 200;

/** Tool names of the Repowise MCP servers: a user-level one, the plugin's, the hosted connector. */
const REPOWISE_TOOL = /^mcp__(?:repowise|plugin_repowise_repowise|claude_ai_repowise)__(\w+)$/i;
/** Lens's own MCP calls fire tool events too; they are not Claude's steps. */
const OWN_CALL_PREFIX = "toolu_plugin_";

export type ActivityKind = "repowise" | "read" | "edit" | "search" | "shell" | "other";

export interface Activity {
  /** The call's tool_use_id. */
  id: string;
  kind: ActivityKind;
  /** The tool's short name: `get_context`, `Read`, `Bash`. */
  tool: string;
  /** Set on a subagent's call. */
  agentId: string | null;
  startedAt: number;
  endedAt: number | null;
  /** Arguments as sent, compacted to a few named values. */
  args: ReadonlyArray<readonly [string, string]>;
  /** Files the call worked on, relative to the session's directory when inside it. */
  paths: readonly string[];
  /** The files a Repowise call asked about (its `targets`), relative like `paths`. */
  asked: readonly string[];
  /** A shell call's command, for which tests it ran. */
  command: string | null;
  isError: boolean;
  /** UTF-8 bytes of the result Claude received; null until it lands. */
  bytes: number | null;
  reply: ReplySummary | null;
  /** For a read or edit: the Repowise reply that named this file first this session. */
  namedBy: Naming | null;
  /** An edit's size from its arguments: lines added less removed, or lines written. */
  lines: { delta: number } | { written: number } | null;
  /** Files a search returned, and (up to a cap) which, relative like `paths`. */
  hits: number | null;
  hitPaths: readonly string[];
}

export interface Naming {
  id: string;
  /** The Repowise tool whose reply named the file. */
  tool: string;
  at: number;
  turn: number;
}

export type TurnEnd = "answer" | "stopped" | "failed";

export interface FlowTurn {
  /** 1 for the session's first turn. */
  seq: number;
  turnId: string | null;
  /** The prompt's first characters; "" for a turn Lens did not see start. */
  prompt: string;
  startedAt: number;
  endedAt: number | null;
  durationMs: number | null;
  /** How the turn ended: an answer, stopped (aborted), or did not finish (refusal, error); null while open. */
  end: TurnEnd | null;
  activities: readonly Activity[];
  dropped: number;
}

/** What the index says an edited file reaches (the depth-1 blast radius), repo-relative. */
export interface BlastFacts {
  /** Files that import it directly. */
  importers: readonly string[];
  /** Files that usually change with it, strongest first, with the server's 0 to 1 score. */
  cochange: ReadonlyArray<{ path: string; score: number }>;
  /** Tests that reach it: how many, which (as emitted), and whether measured or inferred. */
  tests: { total: number; files: readonly string[]; basis: "measured" | "inferred" } | null;
}

export interface Totals {
  turns: number;
  repowise: number;
  reads: number;
  edits: number;
}

export interface FlowState {
  turns: readonly FlowTurn[];
  /** Totals of the turns folded away. */
  earlier: Totals;
  /** Paths Repowise named, by `pathKey`, at their first naming. */
  named: Readonly<Record<string, Naming>>;
  /** Blast radius facts of edited files, by `pathKey`, as the map's request returned them. */
  blast: Readonly<Record<string, BlastFacts>>;
  /** The Repowise call whose detail is open. */
  open: string | null;
}

export const initialFlow: FlowState = {
  turns: [],
  earlier: { turns: 0, repowise: 0, reads: 0, edits: 0 },
  named: {},
  blast: {},
  open: null,
};

/** A tool call as `tool.call` sees it: its arguments ride beside these keys. */
export interface ToolCall {
  tool: string;
  tool_use_id: string;
  agentId?: string | undefined;
  [arg: string]: unknown;
}

export type FlowAction =
  | { type: "turnStarted"; turnId: string | null; prompt: string; at: number }
  | { type: "turnEnded"; at: number; durationMs: number | null; end: TurnEnd }
  | { type: "toolStarted"; activity: Activity }
  | {
      type: "toolEnded";
      id: string;
      at: number;
      isError: boolean;
      bytes: number | null;
      reply: ReplySummary | null;
      hits: number | null;
      hitPaths: readonly string[];
    }
  | { type: "blastLanded"; path: string; facts: BlastFacts }
  | { type: "toggle"; id: string }
  | { type: "step"; by: 1 | -1 };

const END_OF: Record<string, TurnEnd> = { answer: "answer", aborted: "stopped", refusal: "failed", error: "failed" };

/** How a `turn.complete` ended the turn, from its `reason` (and `isAborted` when no reason came). */
export function turnEnd(reason: string | undefined, isAborted: boolean | undefined): TurnEnd {
  return END_OF[reason ?? ""] ?? (isAborted === true ? "stopped" : "answer");
}

/** The Repowise tool a name calls (`get_context`), or null for any other tool. */
export function repowiseTool(name: string): string | null {
  return REPOWISE_TOOL.exec(name)?.[1] ?? null;
}

const KIND_OF: Record<string, ActivityKind> = {
  Read: "read",
  Edit: "edit",
  MultiEdit: "edit",
  Write: "edit",
  NotebookEdit: "edit",
  Grep: "search",
  Glob: "search",
  Bash: "shell",
};

/** The arguments worth a glance, in this order. */
const ARG_KEYS = ["targets", "query", "question", "id", "file_path", "notebook_path", "pattern", "path", "command", "url", "description"];
const PATH_ARGS = new Set(["file_path", "notebook_path", "path"]);

/** `path` relative to `root` with `/` separators when inside it; else as given, slashed. */
export function shortPath(path: string, root: string | null): string {
  const slashed = path.replace(/\\/g, "/");
  if (root === null) return slashed;
  const base = root.replace(/\\/g, "/").replace(/\/+$/, "");
  const inside = slashed.toLowerCase().startsWith(`${base.toLowerCase()}/`);
  return inside ? slashed.slice(base.length + 1) : slashed;
}

function argValue(key: string, v: unknown, root: string | null): string | null {
  if (typeof v === "string") return (PATH_ARGS.has(key) ? shortPath(v, root) : v.replace(/\s+/g, " ")).slice(0, ARG_CHARS);
  if (Array.isArray(v)) return v.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(", ").slice(0, ARG_CHARS);
  if (v === undefined || v === null) return null;
  return JSON.stringify(v).slice(0, ARG_CHARS);
}

function compactArgs(e: ToolCall, root: string | null): Array<readonly [string, string]> {
  const out: Array<readonly [string, string]> = [];
  for (const key of ARG_KEYS) {
    const value = argValue(key, e[key], root);
    if (value !== null && out.length < MAX_ARGS) out.push([key, value]);
  }
  return out;
}

const lineCount = (s: unknown): number | null => (typeof s === "string" ? s.split("\n").length : null);

function editLines(e: ToolCall): Activity["lines"] {
  const written = e.tool === "Write" ? lineCount(e["content"]) : null;
  if (written !== null) return { written };
  const before = lineCount(e["old_string"]);
  const after = lineCount(e["new_string"]);
  return before !== null && after !== null ? { delta: after - before } : null;
}

/** The files a Repowise call names in its `targets` (symbol ids left out), as paths. */
function askedFiles(e: ToolCall, root: string | null): string[] {
  const targets = e["targets"];
  const list: unknown[] = Array.isArray(targets) ? targets : [];
  return list.filter((t): t is string => typeof t === "string" && !t.includes("::")).map((t) => shortPath(t, root));
}

/** A call Claude made: it has an id, and not one of Lens's own. */
function isClaudeCall(e: ToolCall): boolean {
  return typeof e.tool_use_id === "string" && typeof e.tool === "string" && !e.tool_use_id.startsWith(OWN_CALL_PREFIX);
}

/**
 * The activity a tool call starts, or null for Lens's own calls. `root` is
 * the session's directory, which paths are shown relative to.
 */
export function activityOf(e: ToolCall, at: number, root: string | null): Activity | null {
  if (!isClaudeCall(e)) return null;
  const rw = repowiseTool(e.tool);
  const kind: ActivityKind = rw !== null ? "repowise" : (KIND_OF[e.tool] ?? "other");
  return { ...BLANK, id: e.tool_use_id, kind, tool: rw ?? e.tool, agentId: agentOf(e), startedAt: at, args: compactArgs(e, root), ...BY_KIND[kind](e, root) };
}

/** An activity's fields before anything about its call is known. */
const BLANK: Omit<Activity, "id" | "kind" | "tool" | "agentId" | "startedAt" | "args"> = {
  endedAt: null,
  paths: [],
  asked: [],
  command: null,
  isError: false,
  bytes: null,
  reply: null,
  namedBy: null,
  lines: null,
  hits: null,
  hitPaths: [],
};

const agentOf = (e: ToolCall): string | null => (typeof e.agentId === "string" ? e.agentId : null);

/** The file a file tool works on, relative to `root`. */
function fileOf(e: ToolCall, root: string | null): Pick<Activity, "paths"> {
  const file = e["file_path"] ?? e["notebook_path"];
  return { paths: typeof file === "string" ? [shortPath(file, root)] : [] };
}

/** A shell call's command, kept to see which tests it ran. */
function commandOf(e: ToolCall): Pick<Activity, "command"> {
  const command = e["command"];
  return { command: typeof command === "string" ? command.slice(0, COMMAND_CHARS) : null };
}

/** What each kind of call adds to its activity. */
const BY_KIND: Record<ActivityKind, (e: ToolCall, root: string | null) => Partial<Activity>> = {
  repowise: (e, root) => ({ ...fileOf(e, root), asked: askedFiles(e, root) }),
  read: fileOf,
  edit: (e, root) => ({ ...fileOf(e, root), lines: editLines(e) }),
  search: fileOf,
  shell: (e) => commandOf(e),
  other: fileOf,
};

/**
 * Files a search returned, from what `next(e)` resolved to: the count, and
 * the names relative to `root` (a Grep that printed lines names none).
 */
export function searchHits(outcome: unknown, root: string | null): { hits: number | null; hitPaths: string[] } {
  const result = (outcome as { result?: { filenames?: unknown; numFiles?: unknown } } | null)?.result;
  if (typeof result !== "object" || result === null) return { hits: null, hitPaths: [] };
  if (!Array.isArray(result.filenames)) return { hits: typeof result.numFiles === "number" ? result.numFiles : null, hitPaths: [] };
  const names = result.filenames.filter((f): f is string => typeof f === "string");
  return { hits: names.length, hitPaths: names.slice(0, MAX_HIT_PATHS).map((f) => shortPath(f, root)) };
}

/** The text of a call's outcome (`next(e)`'s `text`): what Claude received, or null. */
export function outcomeText(outcome: unknown): string | null {
  const text = (outcome as { text?: unknown } | null)?.text;
  return typeof text === "string" ? text : null;
}

/**
 * The end of a tool call, from what `next(e)` resolved to (null when it
 * threw): its time, whether it failed, the bytes Claude received (counted,
 * never kept), a Repowise reply's summary, a search's hits. Null for Lens's
 * own calls.
 */
export function toolEnded(e: ToolCall, outcome: unknown, at: number, root: string | null): FlowAction | null {
  if (!isClaudeCall(e)) return null;
  const rw = repowiseTool(e.tool);
  const text = outcomeText(outcome);
  const isError = outcome === null || (outcome as { isError?: unknown } | undefined)?.isError === true;
  const summary = rw === null || text === null ? null : summarizeReply(rw, text);
  // Named paths compare repo-relative: an absolute one under the root is made relative here.
  const reply = summary === null ? null : { ...summary, paths: summary.paths.map((p) => shortPath(p, root)) };
  const bytes = summary?.bytes ?? (text === null ? null : new TextEncoder().encode(text).length);
  return { type: "toolEnded", id: e.tool_use_id, at, isError, bytes, reply, ...searchHits(outcome, root) };
}

/** A key matching a path however it was written: `/` separators, no case. */
export function pathKey(path: string): string {
  // Ceiling: case is folded everywhere, so two files differing only in case share a key.
  return path.replace(/\\/g, "/").replace(/^\.\//, "").toLowerCase();
}

/**
 * The first naming of exactly this path. Both sides are relative to the same
 * root (`shortPath`), so a file outside it never matches a reply's path.
 */
function namingOf(named: FlowState["named"], path: string): Naming | null {
  return named[pathKey(path)] ?? null;
}

/** Folds paths a reply named into the session's namings; first naming wins. */
function withNamed(named: FlowState["named"], paths: readonly string[], naming: Naming): FlowState["named"] {
  let out = named;
  let size = Object.keys(named).length;
  for (const p of paths) {
    const key = pathKey(p);
    if (out[key] !== undefined || size >= MAX_NAMED) continue;
    if (out === named) out = { ...named };
    (out as Record<string, Naming>)[key] = naming;
    size++;
  }
  return out;
}

export interface TurnTotals {
  /** Repowise calls made, and those that came back without an error, with their time. */
  repowise: number;
  answered: number;
  answeredMs: number;
  reads: number;
  edits: number;
  /** Distinct files read or edited, and how many of them a Repowise reply named first. */
  opened: number;
  named: number;
  /** An edit landed in a file a Repowise reply named first. */
  editNamed: boolean;
}

export const isFile = (a: Activity): boolean => a.kind === "read" || a.kind === "edit";
const isAnswered = (a: Activity): boolean => a.kind === "repowise" && a.endedAt !== null && !a.isError;

/** Counts for a turn: Repowise calls and their time, distinct files read and edited, and how many Repowise named first. */
export function turnTotals(turn: FlowTurn): TurnTotals {
  const files = turn.activities.filter(isFile);
  const keys = (list: Activity[]) => new Set(list.map((a) => pathKey(a.paths[0] ?? "")));
  const answered = turn.activities.filter(isAnswered);
  const edits = files.filter((a) => a.kind === "edit");
  return {
    repowise: turn.activities.filter((a) => a.kind === "repowise").length,
    answered: answered.length,
    answeredMs: answered.reduce((ms, a) => ms + (a.endedAt as number) - a.startedAt, 0),
    reads: keys(files.filter((a) => a.kind === "read")).size,
    edits: keys(edits).size,
    opened: keys(files).size,
    named: keys(files.filter((a) => a.namedBy !== null)).size,
    editNamed: edits.some((a) => a.namedBy !== null),
  };
}

function fold(earlier: Totals, turn: FlowTurn): Totals {
  const t = turnTotals(turn);
  return { turns: earlier.turns + 1, repowise: earlier.repowise + t.repowise, reads: earlier.reads + t.reads, edits: earlier.edits + t.edits };
}

function withTurn(state: FlowState, turn: FlowTurn): FlowState {
  const turns = [...state.turns, turn];
  if (turns.length <= MAX_TURNS) return { ...state, turns };
  const gone = turns.shift() as FlowTurn;
  return { ...state, turns, earlier: fold(state.earlier, gone) };
}

/**
 * Blocks the harness puts around what the person typed: agent hand-backs,
 * system reminders, task notifications, slash-command wrappers. A tag may
 * arrive escaped (`<\\agent-message`); one never closed runs to the end.
 */
const INJECTED = /\s*<\\?((?:agent-message|system-reminder|task-notification|local-command-[\w-]+|command-[\w-]+))\b[^>]*>[\s\S]*?(?:<\/\1>|$)\s*/g;

/** The person's own words in a turn's text: injected blocks removed wherever they sit; "" when nothing they typed remains. */
export function userWords(text: string): string {
  return text.replace(INJECTED, " ").trim();
}

function newTurn(state: FlowState, turnId: string | null, prompt: string, at: number): FlowTurn {
  const seq = (state.turns.at(-1)?.seq ?? state.earlier.turns) + 1;
  return { seq, turnId, prompt: userWords(prompt).slice(0, PROMPT_CHARS), startedAt: at, endedAt: null, durationMs: null, end: null, activities: [], dropped: 0 };
}

/** The open turn, or a new one when Lens did not see this turn start. */
function openTurn(state: FlowState, at: number): FlowState {
  const last = state.turns.at(-1);
  return last !== undefined && last.endedAt === null ? state : withTurn(state, newTurn(state, null, "", at));
}

function mapLast(state: FlowState, f: (t: FlowTurn) => FlowTurn): FlowState {
  const last = state.turns.at(-1);
  const next = last === undefined ? last : f(last);
  return next === last ? state : { ...state, turns: [...state.turns.slice(0, -1), next as FlowTurn] };
}

/** Rewrites the activity `id` wherever it is; the state as it was when no turn holds it. */
function mapActivity(state: FlowState, id: string, f: (a: Activity) => Activity): FlowState {
  const i = state.turns.findIndex((t) => t.activities.some((a) => a.id === id));
  if (i < 0) return state;
  const turn = state.turns[i] as FlowTurn;
  const activities = turn.activities.map((a) => (a.id === id ? f(a) : a));
  const turns = state.turns.map((t, j) => (j === i ? { ...turn, activities } : t));
  return { ...state, turns };
}

/** The turn with one more activity, the oldest dropped (and counted) past the cap. */
function pushActivity(t: FlowTurn, activity: Activity): FlowTurn {
  const all = [...t.activities, activity];
  const over = all.length - MAX_ACTIVITIES;
  return over > 0 ? { ...t, activities: all.slice(over), dropped: t.dropped + over } : { ...t, activities: all };
}

function append(state: FlowState, activity: Activity): FlowState {
  return mapLast(openTurn(state, activity.startedAt), (t) => pushActivity(t, activity));
}

/** Ends the open turn, if any, as `end`. */
function closeTurn(state: FlowState, at: number, durationMs: number | null, end: TurnEnd): FlowState {
  return mapLast(state, (t) => (t.endedAt !== null ? t : { ...t, endedAt: at, durationMs: durationMs ?? at - t.startedAt, end }));
}

function started(state: FlowState, activity: Activity): FlowState {
  const namedBy = isFile(activity) && activity.paths[0] !== undefined ? namingOf(state.named, activity.paths[0]) : null;
  return append(state, { ...activity, namedBy });
}

function ended(state: FlowState, action: Extract<FlowAction, { type: "toolEnded" }>): FlowState {
  const { id, at, isError, bytes, reply, hits, hitPaths } = action;
  const next = mapActivity(state, id, (a) => ({ ...a, endedAt: at, isError, bytes, reply, hits, hitPaths }));
  if (next === state || reply === null || isError) return next;
  const turn = next.turns.find((t) => t.activities.some((a) => a.id === id)) as FlowTurn;
  return { ...next, named: withNamed(next.named, reply.paths, { id, tool: reply.tool, at, turn: turn.seq }) };
}

/** The rows a press or a hotkey can open: Repowise calls, oldest first. */
export function openable(state: FlowState): string[] {
  return state.turns.flatMap((t) => t.activities.filter((a) => a.kind === "repowise").map((a) => a.id));
}

function stepped(state: FlowState, by: 1 | -1): FlowState {
  const ids = openable(state);
  if (ids.length === 0) return state;
  const at = state.open === null ? -1 : ids.indexOf(state.open);
  const i = at < 0 ? (by === 1 ? 0 : ids.length - 1) : Math.min(ids.length - 1, Math.max(0, at + by));
  return ids[i] === state.open ? state : { ...state, open: ids[i] as string };
}

export function reduceFlow(state: FlowState, action: FlowAction): FlowState {
  switch (action.type) {
    case "turnStarted": {
      // A prompt while the last turn is still open: that one was stopped.
      const closed = closeTurn(state, action.at, null, "stopped");
      return withTurn(closed, newTurn(closed, action.turnId, action.prompt, action.at));
    }
    case "turnEnded":
      return closeTurn(state, action.at, action.durationMs, action.end);
    case "toolStarted":
      return started(state, action.activity);
    case "toolEnded":
      return ended(state, action);
    case "blastLanded":
      return { ...state, blast: { ...state.blast, [pathKey(action.path)]: action.facts } };
    case "toggle":
      return { ...state, open: state.open === action.id ? null : action.id };
    case "step":
      return stepped(state, action.by);
  }
}

/** Every activity kept, oldest first, across turns. */
export function activities(state: FlowState): Activity[] {
  return state.turns.flatMap((t) => [...t.activities]);
}

/** One place Claude's attention went: a file read or edited, or a file a search returned. */
export interface Visit {
  path: string;
  kind: "read" | "edit" | "search-hit";
  /** Increasing per read, edit or search; every hit of one search shares its search's number. */
  order: number;
  at: number;
  turn: number;
}

/**
 * Claude's visits this session, in order, for the map: reads and
 * edits each get the next number, and a search's hits all share one. Paths
 * are as `activityOf` and `searchHits` kept them (relative to the session's
 * root, `/` separators). Failed calls are left out.
 */
export function visits(state: FlowState): Visit[] {
  const out: Visit[] = [];
  let order = 0;
  const steps = state.turns.flatMap((t) => t.activities.map((a) => [a, t.seq] as const));
  for (const [a, turn] of steps) {
    const found = visitsOf(a, turn, order + 1);
    if (found.length > 0) order++;
    out.push(...found);
  }
  return out;
}

/** One activity's visits, all numbered `order`: a read or edit's file, or a search's hits. */
function visitsOf(a: Activity, turn: number, order: number): Visit[] {
  if (a.isError) return [];
  if (a.kind === "search") return a.hitPaths.map((path): Visit => ({ path, kind: "search-hit", order, at: a.endedAt ?? a.startedAt, turn }));
  const path = a.paths[0];
  return (a.kind === "read" || a.kind === "edit") && path !== undefined ? [{ path, kind: a.kind, order, at: a.startedAt, turn }] : [];
}

/** Whether Claude is in a turn now. */
export function isWorking(state: FlowState): boolean {
  const last = state.turns.at(-1);
  return last !== undefined && last.endedAt === null;
}

/** A Repowise call still waiting on its reply. */
export function inFlight(state: FlowState): Activity | null {
  return state.turns.at(-1)?.activities.find((a) => a.kind === "repowise" && a.endedAt === null) ?? null;
}
