/**
 * The mod's entry, and the only file that touches `$`. Each hook builds a Host
 * from its own `$`, feeds events to the model, and redraws. Lens only
 * observes: every hook passes its event on unchanged.
 */

import { getSavings } from "@repowise-dev/api-client/costs";
import { discover, readFreshness } from "./data/discovery";
import { callTool, fetchFileContext, isOwnLensCall, resetMcp, warmMcp } from "./data/mcp";
import { withTimeout } from "./data/transport";
import { fit } from "./format";
import type { Host } from "./host";
import type {
  CheckNext,
  ModApi,
  On,
  PluginOptions,
  PostToolUseEvent,
  RenderEvent,
  SpinnerEvent,
  ToolCallEvent,
  ToolCheckEvent,
  ToolResultEvent,
  ToolUseEvent,
  TurnCompleteEvent,
} from "./mod-api";
import { EDIT_TOOLS, fileTarget, fromTurnComplete, notesFromAugment, savingsSince, savingsTotals } from "./model/events";
import { initialSession, reduce, type SavingsDelta, type SessionAction, type SessionState } from "./model/session";
import { bandView } from "./views/band";
import { materialize, type ElementTable, type Node } from "./views/elements";
import { marginView } from "./views/margin";
import { isFileEdit, type ChangeRisk } from "./model/review";
import { REVIEW_TIMEOUT_S } from "./views/copy";
import { PRESS, directiveRows, reviewText, runTestsText, withCard } from "./views/review";
import { spinnerSuffix } from "./views/spinner";
import { bashText, parseSqueeze, type Squeeze } from "./model/squeeze";
import { squeezeView } from "./views/squeeze";

const PROCESS_TIMEOUT_MS = 5_000;
/** The savings route scans transcripts: 0.24 s on a quiet store, 5-12 s on a busy one. */
const SAVINGS_TIMEOUT_MS = 30_000;
const SAVINGS_COOLDOWN_MS = 60_000;
/** The server's key in the plugin's .mcp.json. */
const MCP_SERVER_KEY = "repowise";

let state: SessionState = initialSession;
/** Bumped per session, so a refresh started in an earlier one never lands in this one. */
let generation = 0;
let running = false;
/** A refresh was asked for while one ran: run once more after it. */
let dirty = false;
let latest: Bound | null = null;
/**
 * Once the server has connected the CLI is known to exist; a later connect
 * can fail while the session shuts its servers down, which is not "no CLI".
 */
let mcpConnected = false;
/** The ledger at this session's first read, and whose; the band shows growth past it. */
let savingsBase: { repoId: string; totals: SavingsDelta } | null = null;
let savingsBusy = false;
let savingsAskedAt = Number.NEGATIVE_INFINITY;
/** Each Bash result's squeeze, kept once found: a row redraws on every scroll. */
const squeezes = new Map<string, Squeeze>();
/** Files whose context is being fetched, or that the index does not know. */
const contextAsked = new Set<string>();
/** The `lens_review` toggle, fixed for one activation. */
let reviewOn = true;
/** Bumped when a main turn starts, so a review still in flight for the last one is dropped. */
let reviewGeneration = 0;
/** Edits that landed since the main turn started. */
let editsThisTurn = 0;

/** A review started during this turn: how many of its edits it covers, and its outcome. */
interface StartedReview {
  gen: number;
  covers: number;
  settled: boolean;
  /** Resolves once `$.mcp.call` has been invoked. */
  callStarted: Promise<void>;
  /** Never rejects: a failure is a `reviewFailed` action. */
  outcome: Promise<SessionAction>;
}
let started: StartedReview | null = null;
/**
 * How long an edit's tool.call stays live waiting for its review call to
 * start (the server name is usually resolved already, so this is microtasks).
 * A call that starts later may be refused; the turn's end then retries.
 */
const START_WAIT_MS = 1_000;
/** Cut so an error stays one line under the answer. */
const ERROR_CELLS = 160;

/** What a background refresh needs from a hook's `$`, as closures (the engine forbids keeping `$` itself). */
interface Bound {
  host: Host;
  redraw(): void;
  /** Debug log only (`--debug-file`), never the user's screen. */
  debug(message: string): void;
}

// Built from the calling hook's own `$`. REST through `$.http` from a later
// continuation of these closures works; an MCP call works only when it starts
// while a Lens hook is live (one started later is refused), so MCP calls are
// started synchronously from inside a hook (see data/mcp.ts).
function bind($: ModApi): Bound {
  const host: Host = {
    session: { cwd: () => $.session.cwd() },
    fs: {
      read: (path) => $.fs.read(path),
      exists: (path) => $.fs.exists(path),
    },
    process: {
      run: (argv, cwd) => $.process.run(argv, { cwd, timeoutMs: PROCESS_TIMEOUT_MS }),
    },
    http: (url, init) => {
      const request: { method?: string; headers?: Record<string, string>; body?: string } = {};
      if (init.method !== undefined) request.method = init.method;
      if (init.headers !== undefined) request.headers = init.headers;
      if (init.body !== undefined) request.body = init.body;
      return $.http.fetch(url, request);
    },
    mcp: {
      connect: async () => (mcpConnected ||= (await $.mcp.connect(MCP_SERVER_KEY)).isConnected),
      server: async () => {
        const r = await $.mcp.connect(MCP_SERVER_KEY);
        return r.isConnected && typeof r.server === "string" ? r.server : null;
      },
      call: (server, tool, args) => $.mcp.call(server, tool, args),
    },
  };
  return {
    host,
    redraw: () => $.ui.invalidate("ui.render"),
    debug: (message) => $.ui.log(`lens: ${message}`, { to: "debug" }),
  };
}

function dispatch(b: Bound, action: SessionAction): void {
  const next = reduce(state, action);
  if (next === state) return;
  state = next;
  b.redraw();
}

async function refreshOnce(b: Bound, gen: number): Promise<void> {
  const h = b.host;
  const found = await discover(h);
  const indexed = found.mode === "full" || found.mode === "lite";
  const freshness = indexed && found.repoRoot !== null ? await readFreshness(h, found.repoRoot) : null;
  if (gen !== generation) return;
  if (indexed) warmMcp(h);
  const action: SessionAction = { type: "discovered", mode: found.mode, freshness, repoRoot: found.repoRoot };
  if (found.liteReason !== undefined) action.liteReason = found.liteReason;
  dispatch(b, action);
  if (found.repoId !== undefined) refreshSavings(b, found.repoId, gen);
}

/**
 * Reads the savings ledger in the background, never awaited by a hook or a
 * render. The first read of a session is the baseline; later reads, at most
 * once a minute, become the band's delta.
 * Ceiling: the baseline lands a moment after the session starts, so savings
 * made before that first read are not counted.
 */
function refreshSavings(b: Bound, repoId: string, gen: number): void {
  const now = Date.now();
  if (savingsBusy || now - savingsAskedAt < SAVINGS_COOLDOWN_MS) return;
  savingsBusy = true;
  savingsAskedAt = now;
  withTimeout(getSavings(repoId), SAVINGS_TIMEOUT_MS, "savings")
    .then((s) => {
      if (gen !== generation) return;
      const totals = savingsTotals(s);
      const delta = savingsBase?.repoId === repoId ? savingsSince(totals, savingsBase.totals) : null;
      if (delta !== null) {
        dispatch(b, { type: "savings", delta });
        return;
      }
      // First read, another repo, or a ledger that shrank: start counting again from here.
      if (savingsBase !== null) dispatch(b, { type: "savings", delta: { tokens: 0, inferredTokens: 0, usd: 0 } });
      savingsBase = { repoId, totals };
    })
    .catch((err: unknown) => b.debug(`savings failed: ${String(err)}`))
    .finally(() => (savingsBusy = false));
}

/** Re-reads mode and freshness in the background, one run at a time. */
function refresh(b: Bound): void {
  latest = b;
  if (running) {
    dirty = true;
    return;
  }
  running = true;
  dirty = false;
  refreshOnce(b, generation)
    // Keep the last good state; the next turn tries again.
    .catch((err: unknown) => b.debug(`refresh failed: ${String(err)}`))
    .finally(() => {
      running = false;
      if (dirty && latest !== null) refresh(latest);
    });
}

async function onSessionStart($: ModApi, e: unknown, next: (e: unknown) => Promise<unknown>): Promise<unknown> {
  const b = bind($);
  try {
    generation++;
    state = initialSession;
    reviewGeneration++;
    editsThisTurn = 0;
    started = null;
    contextAsked.clear();
    squeezes.clear();
    savingsBase = null;
    savingsAskedAt = Number.NEGATIVE_INFINITY;
    resetMcp();
    warmMcp(b.host);
    refresh(b);
  } catch (err) {
    // Lens stays quiet; the session is unaffected.
    b.debug(`session.start failed: ${String(err)}`);
  }
  return next(e);
}

// Also the first refresh when the module loaded after the session started.
async function onTurnComplete(
  $: ModApi,
  e: TurnCompleteEvent,
  next: (e: TurnCompleteEvent) => Promise<unknown>,
): Promise<unknown> {
  const b = bind($);
  try {
    const action = fromTurnComplete(e);
    if (action !== null) {
      dispatch(b, action);
      refresh(b);
    }
  } catch (err) {
    b.debug(`turn.complete failed: ${String(err)}`);
  }
  const result = await next(e);
  if (!reviewOn || e.agentId !== undefined || editsThisTurn === 0) return result;
  // An interrupted or failed turn may have stopped mid-change: no review.
  if (e.isAborted === true || e.reason === "aborted" || e.reason === "error") return result;
  try {
    const card = await finishReview(b);
    return card === null ? result : withCard(result, e.answer ?? "", card);
  } catch (err) {
    b.debug(`change review failed: ${String(err)}`);
    return result;
  }
}

async function fetchReview(b: Bound, host: Host): Promise<SessionAction> {
  try {
    const risk = await callTool<ChangeRisk>(host, "get_change_risk", {}, { timeoutMs: REVIEW_TIMEOUT_S * 1000 });
    return { type: "reviewed", risk };
  } catch (err) {
    b.debug(`change review failed: ${String(err)}`);
    const message = fit((err instanceof Error ? err.message : String(err)).split("\n")[0] ?? "", ERROR_CELLS);
    return { type: "reviewFailed", reason: err instanceof Error && err.name === "TimeoutError" ? "timeout" : "error", message };
  }
}

/**
 * Starts a review of the working tree, not awaited. Only ever called while a
 * Lens hook is live (an edit's tool.call, or turn.complete), which is when the
 * engine approves the call.
 */
function startReview(b: Bound): StartedReview {
  let markStarted: () => void = () => undefined;
  const callStarted = new Promise<void>((resolve) => (markStarted = resolve));
  const host: Host = {
    ...b.host,
    mcp: {
      ...b.host.mcp,
      call: (server, tool, args) => {
        markStarted();
        return b.host.mcp.call(server, tool, args);
      },
    },
  };
  const review: StartedReview = {
    gen: reviewGeneration,
    covers: editsThisTurn,
    settled: false,
    callStarted,
    outcome: fetchReview(b, host),
  };
  void review.outcome.then(() => {
    review.settled = true;
  });
  started = review;
  return review;
}

/**
 * After an edit lands: start a review now unless one is already in flight,
 * and keep the calling hook live until its call has started (or a short cap).
 */
async function reviewAfterEdit(b: Bound): Promise<void> {
  editsThisTurn++;
  dispatch(b, { type: "fileEdited" });
  if (started !== null && started.gen === reviewGeneration && !started.settled) return;
  const review = startReview(b);
  let timer: ReturnType<typeof setTimeout> | undefined;
  const cap = new Promise<void>((resolve) => (timer = setTimeout(resolve, START_WAIT_MS)));
  await Promise.race([review.callStarted, review.outcome, cap]);
  clearTimeout(timer);
}

/**
 * At the end of the turn: the started review when it covers every edit of the
 * turn (usually already landed), else one more. Null when a new turn started
 * meanwhile.
 */
async function finishReview(b: Bound): Promise<string | null> {
  const gen = reviewGeneration;
  const covering = started !== null && started.gen === gen && started.covers === editsThisTurn;
  const review = covering && started !== null ? started : startReview(b);
  if (!review.settled) dispatch(b, { type: "reviewStarted" });
  let action = await review.outcome;
  // A review started during an edit can fail where one awaited here would not
  // (its call started after that hook returned): try once more, awaited.
  if (covering && action.type === "reviewFailed" && action.reason === "error" && gen === reviewGeneration) {
    action = await startReview(b).outcome;
  }
  if (gen !== reviewGeneration) return null;
  dispatch(b, action);
  return reviewText(state.review.outcome);
}

async function onTurnStart($: ModApi, e: unknown, next: (e: unknown) => Promise<unknown>): Promise<unknown> {
  try {
    reviewGeneration++;
    editsThisTurn = 0;
    started = null;
    dispatch(bind($), { type: "turnStarted" });
  } catch (err) {
    bind($).debug(`turn.start failed: ${String(err)}`);
  }
  return next(e);
}

/** `Run tests`: a visible prompt naming the tests; nothing reaches Claude without this press. */
// A press runs after the render hook that drew the button returned, with
// that hook's `$`; the docs' own Button examples call `$` from onPress.
function pressRunTests($: ModApi): void {
  try {
    const prompt = runTestsText(state.review.outcome);
    if (prompt === null) return;
    $.prompt.submit({ text: prompt }).catch((err: unknown) => bind($).debug(`run tests failed: ${String(err)}`));
  } catch (err) {
    bind($).debug(`run tests failed: ${String(err)}`);
  }
}

/** `Details`: the review's full directive in the transcript, one row per line, for the user only. */
function pressDetails($: ModApi): void {
  try {
    for (const row of directiveRows(state.review.outcome) ?? []) $.ui.log(row, { to: "transcript" });
  } catch (err) {
    bind($).debug(`details failed: ${String(err)}`);
  }
}

// The band is shared: a mod's tree replaces what the mods after it draw
// (docs: plugins/mods/interface, "Band above the prompt"), so Lens puts its
// rows above the tree `next(e)` returns instead of dropping it.
async function onBand($: ModApi, e: RenderEvent, next: (e: RenderEvent) => Promise<unknown>): Promise<unknown> {
  const theirs = await next(e);
  try {
    const tree = bandView(state, { columns: e.props.bodyColumns ?? 80, hasSurvey: e.props.hasSurvey === true });
    if (tree === null) return theirs;
    const elements = $.ui.resolve(e);
    const ours = materialize(tree, elements, {
      [PRESS.tests]: () => pressRunTests($),
      [PRESS.details]: () => pressDetails($),
    });
    const box = elements["Box"];
    if (theirs === null || theirs === undefined || box === undefined) return ours;
    return box({ flexDirection: "column", children: [ours, theirs] });
  } catch (err) {
    bind($).debug(`band render failed: ${String(err)}`);
    return theirs;
  }
}

/**
 * Fetches a file's context once per session, started here and not awaited:
 * the engine approves Lens's call because it starts while this hook is live,
 * and the spinner shows it from the first render after it lands.
 * Ceiling: cached for the session, so an index update mid-session is not seen.
 */
function fetchContext(b: Bound, file: string): void {
  if (contextAsked.has(file)) return;
  contextAsked.add(file);
  const gen = generation;
  fetchFileContext(b.host, file)
    .then((context) => {
      if (gen !== generation || context === null) return;
      dispatch(b, { type: "contextLoaded", file, context });
    })
    .catch((err: unknown) => {
      // Asked again on the next call: the server may not have connected yet.
      contextAsked.delete(file);
      b.debug(`context for ${file} failed: ${String(err)}`);
    });
}

async function onToolCall($: ModApi, e: ToolCallEvent, next: (e: ToolCallEvent) => Promise<unknown>): Promise<unknown> {
  const b = bind($);
  let file: string | null = null;
  try {
    file = fileTarget(e, state.repoRoot);
    if (file !== null) {
      dispatch(b, { type: "toolStarted", tool: { id: e.tool_use_id, file } });
      fetchContext(b, file);
    }
  } catch (err) {
    b.debug(`tool.call failed: ${String(err)}`);
  }
  let result: unknown;
  try {
    result = await next(e);
  } finally {
    if (file !== null) dispatch(b, { type: "toolEnded", id: e.tool_use_id });
  }
  try {
    if (reviewOn && isFileEdit(e, result)) await reviewAfterEdit(b);
  } catch (err) {
    b.debug(`tool.call failed: ${String(err)}`);
  }
  return result;
}

// The one approval Lens gives: its own read-only lookups. Every other
// question keeps the engine's verdict, untouched.
async function onToolCheck($: ModApi, e: ToolCheckEvent, next: CheckNext): Promise<unknown> {
  try {
    if (isOwnLensCall(e, next.origin?.plugin)) {
      return { decision: "allow", reason: "Repowise Lens: its own read-only index lookup" };
    }
  } catch (err) {
    bind($).debug(`tool.check failed: ${String(err)}`);
  }
  return next(e);
}

// Rewrites only the suffix prop, so the engine keeps drawing (and animating) the line.
async function onSpinner($: ModApi, e: SpinnerEvent, next: (e: SpinnerEvent) => Promise<unknown>): Promise<unknown> {
  let line: string | null = null;
  try {
    line = spinnerSuffix(state);
  } catch (err) {
    bind($).debug(`spinner render failed: ${String(err)}`);
  }
  if (line === null) return next(e);
  const theirs = typeof e.props.suffix === "string" ? e.props.suffix : "";
  return next({ ...e, props: { ...e.props, suffix: `${theirs} ${line}` } });
}

/** Lens's rows drawn under what the engine (and the mods after Lens) drew for the site. */
function below(elements: ElementTable, theirs: unknown, tree: Node): unknown {
  const ours = materialize(tree, elements);
  const box = elements["Box"];
  if (theirs === null || theirs === undefined || box === undefined) return ours;
  return box({ flexDirection: "column", children: [theirs, ours] });
}

// A miss is not kept: an early render can come before the output is whole.
function squeezeOf(e: ToolResultEvent): Squeeze | null {
  const id = e.props.tool_use_id;
  const known = squeezes.get(id);
  if (known !== undefined || e.props.tool !== "Bash") return known ?? null;
  const out = bashText(e.props.output);
  const found = out === null ? null : parseSqueeze(out);
  if (found !== null) squeezes.set(id, found);
  return found;
}

async function onToolResult(
  $: ModApi,
  e: ToolResultEvent,
  next: (e: ToolResultEvent) => Promise<unknown>,
): Promise<unknown> {
  const theirs = await next(e);
  try {
    const s = squeezeOf(e);
    return s === null ? theirs : below($.ui.resolve(e), theirs, squeezeView(s));
  } catch (err) {
    bind($).debug(`squeeze render failed: ${String(err)}`);
    return theirs;
  }
}

// Reads what the augment hook told Claude about this edit, and passes it on untouched.
async function onPostToolUse(
  $: ModApi,
  e: PostToolUseEvent,
  next: (e: PostToolUseEvent) => Promise<unknown>,
): Promise<unknown> {
  const result = await next(e);
  try {
    // Lens's own lookups also pass through here; they are not Claude's edits.
    if (EDIT_TOOLS.has(e.tool_name) && !e.tool_use_id.startsWith("toolu_plugin_")) {
      const notes = notesFromAugment((result as { additionalContext?: unknown } | null)?.additionalContext);
      if (notes.length > 0) dispatch(bind($), { type: "notesFor", id: e.tool_use_id, notes });
    }
  } catch (err) {
    bind($).debug(`PostToolUse failed: ${String(err)}`);
  }
  return result;
}

async function onToolUse($: ModApi, e: ToolUseEvent, next: (e: ToolUseEvent) => Promise<unknown>): Promise<unknown> {
  const theirs = await next(e);
  try {
    const tree = marginView(state.notes[e.props.tool_use_id]);
    return tree === null ? theirs : below($.ui.resolve(e), theirs, tree);
  } catch (err) {
    bind($).debug(`margin render failed: ${String(err)}`);
    return theirs;
  }
}

// `options` are the plugin's userConfig toggles; a change reloads the module.
export function register(on: On, options: PluginOptions = {}): void {
  reviewOn = options["lens_review"] !== false;
  on("session.start", onSessionStart);
  if (reviewOn) on("turn.start", onTurnStart);
  on("turn.complete", onTurnComplete);
  on("ui.render", { component: "AbovePrompt" }, onBand);
  on("tool.call", onToolCall);
  on("tool.check", onToolCheck);
  on("ui.render", { component: "Spinner" }, onSpinner);
  if (options["lens_squeeze"] !== false) on("ui.render", { component: "ToolResult" }, onToolResult);
  if (options["lens_margin"] !== false) {
    on("classic.PostToolUse", onPostToolUse);
    on("ui.render", { component: "ToolUse" }, onToolUse);
  }
}
