/**
 * The mod's entry, and the only file that touches `$`. Each hook builds a Host
 * from its own `$`, feeds events to the model, and redraws. Lens only
 * observes: every hook passes its event on unchanged.
 */

import { getSavings } from "@repowise-dev/api-client/costs";
import { discover, isWindowsPath, readFreshness, type Discovery } from "./data/discovery";
import { callTool, fetchFileContext, isOwnLensCall, resetMcp, warmMcp } from "./data/mcp";
import { withTimeout } from "./data/transport";
import { fit } from "./format";
import type { Host } from "./host";
import { LensMap, type MapIO, type MapRepo } from "./map-controller";
import type {
  CheckNext,
  ModApi,
  On,
  PaneRenderEvent,
  PluginOptions,
  PostToolUseEvent,
  RenderEvent,
  SessionStartEvent,
  SpinnerEvent,
  ToolCallEvent,
  ToolCheckEvent,
  ToolResultEvent,
  ToolUseEvent,
  TurnCompleteEvent,
} from "./mod-api";
import {
  EDIT_TOOLS,
  OBSERVED_TOOLS,
  absolutePath,
  fileTarget,
  fromToolCall,
  fromTurnComplete,
  notesFromAugment,
  savingsSince,
  savingsTotals,
} from "./model/events";
import { hintFor, initialSession, reduce, type SavingsDelta, type SessionAction, type SessionState } from "./model/session";
import { bandView } from "./views/band";
import { materialize, type ElementTable, type Node } from "./views/elements";
import { marginView } from "./views/margin";
import { isFileEdit, isRetryable, shouldReview, type ChangeRisk } from "./model/review";
import { HINTS, MAP_COPY, REVIEW_TIMEOUT_S } from "./views/copy";
import { MAP_KEY } from "./views/mapPane";
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
const PANE_ID = "lens";
/** Rows an inline pane asks for: the tallest inline map (20) plus a legend wrapped once. */
const PANE_ROWS = 28;

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
  /** Never rejects: a failure is a `reviewFailed` action. */
  outcome: Promise<SessionAction>;
}
let started: StartedReview | null = null;
/** The `lens_pane_autoopen` toggle, fixed for one activation. */
let autoOpenOn = false;
/** The last discovery, for the map pane's notice and its repo. */
let discovery: Discovery | null = null;
/** The session's cwd, from its start event, so observing a tool call waits on nothing. */
let cwd: string | null = null;
let map = new LensMap(false);

/** Cut so an error stays one line under the answer. */
const ERROR_CELLS = 160;

/** What a background refresh needs from a hook's `$`, as closures (the engine forbids keeping `$` itself). */
interface Bound extends MapIO {
  host: Host;
}

// Built from the calling hook's own `$`. REST through `$.http` from a later
// continuation of these closures works; an MCP call works only when it starts
// while a Lens hook is live (one started later is refused), so MCP calls are
// started synchronously from inside a hook (see data/mcp.ts). A late
// `$.ui.blit` is not refused either (checked headless from a timer).
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
    blit: (cells, columns, rows) => $.ui.blit({ requestId: PANE_ID, key: MAP_KEY, cells, columns, rows }),
    // No focus: the map has no controls, and the prompt keeps the keyboard.
    openPane: () => $.ui.open({ id: PANE_ID, title: MAP_COPY.title, rows: PANE_ROWS }),
    closePane: () => $.ui.close({ id: PANE_ID }),
  };
}

function mapRepo(d: Discovery): MapRepo | null {
  if (d.mode !== "full" || d.repo === undefined || d.repoRoot === null) return null;
  return { id: d.repo.id, root: d.repoRoot, updatedAt: d.repo.updatedAt, caseInsensitive: isWindowsPath(d.repoRoot) };
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
  discovery = found;
  const action: SessionAction = { type: "discovered", mode: found.mode, freshness, repoRoot: found.repoRoot };
  if (found.liteReason !== undefined) action.liteReason = found.liteReason;
  dispatch(b, action);
  if (found.repo !== undefined) refreshSavings(b, found.repo.id, gen);
  map.setRepo(b, mapRepo(found));
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

async function onSessionStart(
  $: ModApi,
  e: SessionStartEvent,
  next: (e: SessionStartEvent) => Promise<unknown>,
): Promise<unknown> {
  const b = bind($);
  try {
    generation++;
    cwd = typeof e.cwd === "string" ? e.cwd : null;
    map.dispose();
    map = new LensMap(autoOpenOn);
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
    await $.command.register({ name: "lens", description: MAP_COPY.command, immediate: true });
    map.setReducedMotion((await $.settings.read())["prefersReducedMotion"] === true);
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
  noteTurnEnd(b, e);
  const result = await next(e);
  return reviewOn && shouldReview(e, editsThisTurn) ? withReview(b, e, result) : result;
}

function noteTurnEnd(b: Bound, e: TurnCompleteEvent): void {
  try {
    const action = fromTurnComplete(e);
    if (action === null) return;
    map.turnEnded();
    dispatch(b, action);
    refresh(b);
  } catch (err) {
    b.debug(`turn.complete failed: ${String(err)}`);
  }
}

/** The turn's result with the review card beneath the answer, or as it was. */
async function withReview(b: Bound, e: TurnCompleteEvent, result: unknown): Promise<unknown> {
  try {
    const card = await finishReview(b);
    return card === null ? result : withCard(result, e.answer ?? "", card);
  } catch (err) {
    b.debug(`change review failed: ${String(err)}`);
    return result;
  }
}

// callTool starts `$.mcp.call` synchronously (or rejects at once when the
// server name is not resolved yet), so the call starts inside the caller's hook.
async function fetchReview(b: Bound): Promise<SessionAction> {
  try {
    const risk = await callTool<ChangeRisk>(b.host, "get_change_risk", {}, { timeoutMs: REVIEW_TIMEOUT_S * 1000 });
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
  const review: StartedReview = { gen: reviewGeneration, covers: editsThisTurn, settled: false, outcome: fetchReview(b) };
  void review.outcome.then(() => {
    review.settled = true;
  });
  started = review;
  return review;
}

/** After an edit lands: start a review, not awaited, unless one is already in flight. */
function reviewAfterEdit(b: Bound): void {
  editsThisTurn++;
  dispatch(b, { type: "fileEdited" });
  if (started !== null && started.gen === reviewGeneration && !started.settled) return;
  startReview(b);
}

/** The review started this turn, when it covers every edit so far. */
function coveringReview(): StartedReview | null {
  const s = started;
  return s !== null && s.gen === reviewGeneration && s.covers === editsThisTurn ? s : null;
}

/**
 * A review started at an edit can fail where one started here would not (the
 * server name was not resolved yet): that one is tried once more, awaited.
 */
async function outcomeOf(b: Bound, review: StartedReview, reused: boolean): Promise<SessionAction> {
  const action = await review.outcome;
  return reused && isRetryable(action) ? startReview(b).outcome : action;
}

/**
 * At the end of the turn: the started review when it covers every edit of the
 * turn (usually already landed), else one more. Null when a new turn started
 * meanwhile.
 */
async function finishReview(b: Bound): Promise<string | null> {
  const gen = reviewGeneration;
  const reused = coveringReview();
  const review = reused ?? startReview(b);
  if (!review.settled) dispatch(b, { type: "reviewStarted" });
  const action = await outcomeOf(b, review, reused !== null);
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
    const line = map.bandLine();
    const viewport = { columns: e.props.bodyColumns ?? 80, hasSurvey: e.props.hasSurvey === true };
    const tree = bandView(state, viewport, line === null ? [] : [line]);
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
  const file = fileToolStarted(b, e);
  let result: unknown;
  try {
    result = await next(e);
  } finally {
    if (file !== null) dispatch(b, { type: "toolEnded", id: e.tool_use_id });
  }
  editLanded(b, e, result);
  observeTrail($, b, e, result);
  return result;
}

/** Where Claude looked, for the map: recorded from what next(e) returned, waiting on nothing. */
function observeTrail($: ModApi, b: Bound, e: ToolCallEvent, result: unknown): void {
  if (!OBSERVED_TOOLS.has(e.tool)) return;
  try {
    const here = cwd;
    // Loaded after the session started: learn the cwd for the next call, skip this one.
    if (here === null) {
      void $.session.cwd().then(
        (c) => (cwd = c),
        (err: unknown) => b.debug(`cwd failed: ${String(err)}`),
      );
      return;
    }
    const ctx = { cwd: here, isWindows: isWindowsPath(here) };
    const action = fromToolCall(e, result, ctx);
    const raw = e.file_path;
    if (action !== null) map.observe(b, action, typeof raw === "string" ? absolutePath(raw, ctx) : null);
  } catch (err) {
    b.debug(`tool.call observe failed: ${String(err)}`);
  }
}

/** A file tool starting in the repo: the spinner's file, and its context fetched. */
function fileToolStarted(b: Bound, e: ToolCallEvent): string | null {
  try {
    const file = fileTarget(e, state.repoRoot);
    if (file === null) return null;
    dispatch(b, { type: "toolStarted", tool: { id: e.tool_use_id, file } });
    fetchContext(b, file);
    return file;
  } catch (err) {
    b.debug(`tool.call failed: ${String(err)}`);
    return null;
  }
}

function editLanded(b: Bound, e: ToolCallEvent, result: unknown): void {
  try {
    if (reviewOn && isFileEdit(e, result)) reviewAfterEdit(b);
  } catch (err) {
    b.debug(`tool.call failed: ${String(err)}`);
  }
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

async function onLensCommand($: ModApi): Promise<unknown> {
  const b = bind($);
  try {
    if (discovery === null || mapRepo(discovery) === null) refresh(b);
    await map.request(b);
  } catch (err) {
    b.debug(`/lens failed: ${String(err)}`);
  }
  // Nothing in the transcript: the pane is the answer.
  return {};
}

async function onPane($: ModApi, e: PaneRenderEvent, next: (e: PaneRenderEvent) => Promise<unknown>): Promise<unknown> {
  if (e.requestId !== PANE_ID) return next(e);
  const b = bind($);
  try {
    const d = discovery;
    const notice = d === null ? MAP_COPY.looking : HINTS[hintFor(d.mode, d.liteReason) ?? "no-index"];
    const { bodyColumns, placement, scroll } = e.props;
    const tree = map.paneTree(b, { surface: e.surface, notice, bodyColumns, placement, bodyRows: scroll.bodyRows });
    return materialize(tree, $.ui.resolve(e));
  } catch (err) {
    b.debug(`map render failed: ${String(err)}`);
    return next(e);
  }
}

// `options` are the plugin's userConfig toggles; a change reloads the module.
export function register(on: On, options: PluginOptions = {}): void {
  reviewOn = options["lens_review"] !== false;
  autoOpenOn = options["lens_pane_autoopen"] === true;
  on("session.start", onSessionStart);
  if (reviewOn) on("turn.start", onTurnStart);
  on("turn.complete", onTurnComplete);
  on("ui.render", { component: "AbovePrompt" }, onBand);
  on("ui.render", { component: "Pane" }, onPane);
  on("command.run", { command: "lens" }, onLensCommand);
  on("tool.call", onToolCall);
  on("tool.check", onToolCheck);
  on("ui.render", { component: "Spinner" }, onSpinner);
  if (options["lens_squeeze"] !== false) on("ui.render", { component: "ToolResult" }, onToolResult);
  if (options["lens_margin"] !== false) {
    on("classic.PostToolUse", onPostToolUse);
    on("ui.render", { component: "ToolUse" }, onToolUse);
  }
}
