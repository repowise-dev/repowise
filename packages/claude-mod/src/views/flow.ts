/**
 * The Flow tab: a dashboard of the turn built from what only Lens sees. The
 * owl and the turn's status; what each edit reaches that Claude did not look
 * at (before you accept); the files it worked from and how it came to them
 * (working set); what filled its context (a bar of measured bytes); and the
 * Repowise calls, one line each, detail on press. Each section draws only
 * when it has something to say. Readable text keeps the terminal's own
 * foreground or its dim; only marks take an accent, and nothing sets a
 * background. Pure: the clock, the width, the rows, the file cards and the
 * turn's review come in on `FlowViewInput`.
 */

import { fit } from "../format";
import { inFlight, isWorking, openable, pathKey, turnTotals, type Activity, type FlowState, type FlowTurn } from "../model/flow";
import type { ChangeRisk } from "../model/review";
import type { FileContext, Mode } from "../model/session";
import {
  CONTEXT_KINDS,
  answeredBeforeEdit,
  beforeAccept,
  contextBytes,
  firstEditMs,
  workingSet,
  type ContextKind,
  type Gaps,
  type Via,
  type WorkingFile,
} from "../model/turnFacts";
import {
  FLOW_COPY,
  HEALTH_PARTLY,
  answerLine,
  behindParts,
  clockText,
  cochangeLine,
  contextPart,
  droppedSteps,
  durationText,
  earlierLine,
  firstEditText,
  freshnessText,
  hiddenSteps,
  importersLine,
  insideFact,
  introducedLine,
  knowsParts,
  laterSteps,
  moreFiles,
  statusLine,
  testsReachLine,
  turnFooter,
} from "./copy";
import { box, button, text, type Node, type TextProps } from "./elements";
import { packRows } from "./mapPane";
import { OWL_WIDTH, owl, owlEyes, type OwlState } from "./owl";
import { THISTLE, flowTheme, hills, loch, type FlowTheme } from "./theme";

export const FLOW_BOX_ID = "lens-flow";
export const FLOW_NEXT = "lens-flow-next";
export const FLOW_PREV = "lens-flow-prev";
/** How long the owl stays happy after a turn ends. */
export const HAPPY_MS = 3_000;
/** Below this width the working set drops what the index knows. */
export const NARROW = 80;
const MIN_BODY = 3;
/** The quiet mark beside the call Claude is waiting on. */
export const ACTIVE_MARK = "▸";
const GUTTER = 2;
const CLOCK_W = 6;
/** The label column of an open call's detail. */
const DETAIL_W = 20;
const DETAIL_INDENT = GUTTER + CLOCK_W;
/** The empty state's widest line. */
const EMPTY_W = 70;
/** Working-set rows drawn before `+N more`. */
const WORKING_ROWS = 6;
const FILE_W = 44;
const KIND_W = 8;
const VIA_W = 13;
const BAR_W = 60;
/** Facts from a reply drawn on a call's line. */
const BUILT_FROM = 2;
/** Columns a call's target keeps before its facts are cut. */
const TARGET_MIN = 28;
const SEP = " · ";
const EXCERPT_LINE = 160;

export interface FlowViewInput {
  columns: number;
  /** Rows the tab body may take. */
  rows: number;
  now: number;
  /** Reduced motion: the owl holds still. */
  still: boolean;
  mode: Mode | null;
  /** File cards from the index, by repo-relative path. */
  contexts: Readonly<Record<string, FileContext>>;
  /** The change review of this turn, when one ran. */
  review: ChangeRisk | null;
  /** The terminal's theme is light: accents come from the light ramp. Dark when unknown. */
  light?: boolean;
}

/** The Button key of an openable row. */
export const rowKey = (id: string): string => `${FLOW_BOX_ID}-${id}`;

/** Every Button key the tab may draw, with the row it opens (null for the hotkeys). */
export function flowPresses(state: FlowState): Array<[key: string, id: string | null]> {
  return [[FLOW_NEXT, null], [FLOW_PREV, null], ...openable(state).map((id): [string, string] => [rowKey(id), id])];
}

/** A run of text in a row; `flex` is the one cut to fit; `press` makes it a Button. Never a background. */
interface Seg {
  t: string;
  color?: string;
  dim?: boolean;
  flex?: boolean;
  press?: string;
}

/** One drawn line, the call it belongs to, and its height when not one row. */
interface Line {
  node: Node;
  id?: string;
  height?: number;
}

const width = (segs: readonly Seg[]): number => segs.reduce((n, s) => n + s.t.length, 0);

function segNode(s: Seg): Node {
  if (s.press !== undefined) return { type: "Button", props: { key: s.press, label: s.t, plain: true } };
  const props: TextProps = {};
  if (s.color !== undefined) props.color = s.color;
  if (s.dim === true) props.dimColor = true;
  return text(s.t, props);
}

/** Segments fitted to `columns` (the flex one cut), and `right` pushed to the right edge. */
function row(segs: Seg[], columns: number, right: Seg[] = []): Node {
  const fixed = width(segs.filter((s) => s.flex !== true)) + width(right) + (right.length > 0 ? 2 : 0);
  const fitted = segs.map((s) => (s.flex === true ? { ...s, t: fit(s.t, Math.max(1, columns - fixed)) } : s));
  const gap = right.length > 0 ? Math.max(2, columns - width(fitted) - width(right)) : 0;
  const all = gap > 0 ? [...fitted, { t: " ".repeat(gap) }, ...right] : fitted;
  return box({ flexDirection: "row" }, all.filter((s) => s.t !== "").map(segNode));
}

const dimLine = (t: string, columns: number): Node => text(fit(t, columns), { dimColor: true, wrap: "truncate-end" });
const pad = (n: number): Seg => ({ t: " ".repeat(n) });

/** Words in lines of at most `columns`. */
function wrapWords(words: string, columns: number): string[] {
  return packRows(words.split(" "), (w) => w.length, Math.max(1, columns), 1).map((r) => r.join(" "));
}

/** A dim caption over a section's lines; nothing at all when the section has none. */
function section(caption: string, lines: Node[]): Node[] {
  return lines.length === 0 ? [] : [text(caption, { dimColor: true }), ...lines];
}

// The owl and the header.

function owlState(state: FlowState, now: number): OwlState {
  const last = state.turns.at(-1);
  if (last === undefined) return "asleep";
  if (isWorking(state)) return inFlight(state) === null ? "watching" : "asking";
  if (last.end === "stopped" || last.end === "failed") return "stopped";
  return last.endedAt !== null && now - last.endedAt < HAPPY_MS ? "happy" : "asleep";
}

/** `Done · 41 s · edited 1 file` (with a thistle after an answer), or `Working · 0:42`. */
function statusSegs(turn: FlowTurn, now: number, theme: FlowTheme): Seg[] {
  const edited = turnTotals(turn).edits;
  if (turn.endedAt === null) return [{ t: statusLine(null, now - turn.startedAt, edited) }];
  const end = turn.end ?? "answer";
  const line: Seg = { t: statusLine(end, turn.durationMs ?? 0, edited) };
  return end === "answer" ? [line, { t: `  ${THISTLE}`, color: theme.plum }] : [line];
}

function notice(mode: Mode | null): string | null {
  return mode === "no-index" || mode === "no-cli" ? FLOW_COPY.notConnected : null;
}

/**
 * The header: the owl and the status on one line, then the answer at full
 * strength, then the user's own words, dim. Empty, the owl asleep and what
 * the tab will show.
 */
function header(state: FlowState, v: FlowViewInput, theme: FlowTheme): Node[] {
  const turn = state.turns.at(-1);
  const st = owlState(state, v.now);
  const face = box({ flexDirection: "row" }, owl(st, owlEyes(st, v.now, v.still), theme.amber));
  const room = v.columns - OWL_WIDTH - 1;
  const said = notice(v.mode);
  const tail = said === null ? [] : [dimLine(said, v.columns)];
  if (turn === undefined) {
    const [first = "", ...rest] = wrapWords(FLOW_COPY.empty, Math.min(EMPTY_W, room));
    return [box({ flexDirection: "row", columnGap: 1 }, [face, text(first)]), ...rest.map((l) => text(`${" ".repeat(OWL_WIDTH + 1)}${l}`)), ...tail];
  }
  const prompt = turn.prompt.replace(/\s+/g, " ").trim();
  return [
    box({ flexDirection: "row", columnGap: 1 }, [face, row(statusSegs(turn, v.now, theme), room)]),
    text(fit(answerLine(turnTotals(turn)), v.columns), { wrap: "truncate-end" }),
    ...(prompt === "" ? [] : [dimLine(`"${prompt}"`, v.columns)]),
    ...tail,
  ];
}

// Before you accept.

const fileName = (path: string): string => path.split("/").at(-1) ?? path;

function gapLines(g: Gaps): string[] {
  const out: string[] = [];
  if (g.importers !== null) out.push(importersLine(g.importers.total, g.importers.opened));
  if (g.cochange.length > 0) out.push(cochangeLine(g.cochange));
  if (g.tests !== null) out.push(testsReachLine(g.tests.total, g.tests.basis, g.tests.run, fileName(g.path)));
  return out;
}

/** The turn's health from its change review: what it introduced, or that it was compared in part only. */
function healthLines(risk: ChangeRisk | null): string[] {
  const hd = risk?.health_delta;
  if (hd === undefined) return [];
  if (hd.introduced > 0) return [introducedLine(hd.introduced, hd.top_findings.find((f) => f.change === "introduced") ?? null)];
  return hd.status === "partial" ? [HEALTH_PARTLY] : [];
}

function beforeAcceptLines(state: FlowState, turn: FlowTurn, v: FlowViewInput): Node[] {
  const files = beforeAccept(state, turn).flatMap((g) => [
    row([pad(2), { t: g.path, flex: true }, { t: `  ${FLOW_COPY.asOfIndex}`, dim: true }], v.columns),
    ...gapLines(g).map((l) => dimLine(`    ${l}`, v.columns)),
  ]);
  const health = healthLines(v.review).map((l) => text(fit(`  ${l}`, v.columns)));
  return section(FLOW_COPY.beforeAccept, [...files, ...health]);
}

// The working set.

function viaSeg(via: Via, theme: FlowTheme): Seg {
  if (via.kind === "repowise") return { t: via.tool.padEnd(VIA_W), color: theme.plum };
  return { t: (via.kind === "search" ? FLOW_COPY.search : FLOW_COPY.direct).padEnd(VIA_W), dim: true };
}

interface TableContext {
  cards: ReadonlyMap<string, FileContext>;
  columns: number;
  fileW: number;
  theme: FlowTheme;
}

function workingRow(f: WorkingFile, t: TableContext): Node {
  const card = t.cards.get(pathKey(f.path));
  const knows = t.columns < NARROW || card === undefined ? "" : knowsParts(card).join(SEP);
  const kind: Seg = { t: (f.kind === "edited" ? FLOW_COPY.edited : FLOW_COPY.read).padEnd(KIND_W), dim: f.kind === "read" };
  return row([pad(2), { t: fit(f.path, t.fileW).padEnd(t.fileW + 2) }, kind, viaSeg(f.via, t.theme), { t: knows, dim: true, flex: true }], t.columns);
}

function workingLines(state: FlowState, turn: FlowTurn, v: FlowViewInput, theme: FlowTheme): Node[] {
  const files = workingSet(state, turn);
  const longest = Math.max(0, ...files.map((f) => f.path.length));
  const t: TableContext = {
    cards: new Map(Object.entries(v.contexts).map(([p, c]) => [pathKey(p), c])),
    columns: v.columns,
    fileW: Math.min(FILE_W, longest, Math.max(8, v.columns - 2 - KIND_W - VIA_W - 4)),
    theme,
  };
  const rows = files.slice(0, WORKING_ROWS).map((f) => workingRow(f, t));
  const more = files.length > WORKING_ROWS ? [dimLine(`  ${moreFiles(files.length - WORKING_ROWS)}`, v.columns)] : [];
  return section(`${FLOW_COPY.workingSet} · ${FLOW_COPY.asOfIndex}`, [...rows, ...more]);
}

// What filled Claude's context.

/** One shade per kind, so the bar reads without color on any ground; Repowise's carries the plum mark. */
const SHADE: Record<ContextKind, string> = { repowise: "█", read: "▓", search: "▒", shell: "░", other: "·" };

/** Cells per kind, at least one for any that has bytes, summing to about `cells`. */
function cellsOf(bytes: Record<ContextKind, number>, total: number, cells: number): Array<[ContextKind, number]> {
  return CONTEXT_KINDS.filter((k) => bytes[k] > 0).map((k): [ContextKind, number] => [k, Math.max(1, Math.round((bytes[k] / total) * cells))]);
}

function shadeSeg(kind: ContextKind, n: number, theme: FlowTheme): Seg {
  const t = SHADE[kind].repeat(n);
  return kind === "repowise" ? { t, color: theme.plum } : { t };
}

function contextLines(turn: FlowTurn, columns: number, theme: FlowTheme): Node[] {
  const bytes = contextBytes(turn);
  const total = CONTEXT_KINDS.reduce((n, k) => n + bytes[k], 0);
  if (total === 0) return [];
  const cells = cellsOf(bytes, total, Math.min(BAR_W, columns - 4));
  const bar = row([pad(2), ...cells.map(([k, n]) => shadeSeg(k, n, theme))], columns);
  const legend = cells.map(([k]) => `${SHADE[k]} ${contextPart(k, bytes[k])}`);
  const first = firstEditMs(turn);
  const parts = first === null ? legend : [...legend, firstEditText(first)];
  const lines = packRows(parts, (p) => p.length, Math.max(10, columns - 2), 2).map((r) => dimLine(`  ${r.join("  ")}`, columns));
  return section(FLOW_COPY.context, [bar, ...lines]);
}

// The Repowise calls.

interface CallContext {
  turn: FlowTurn;
  columns: number;
  /** The call Claude is waiting on. */
  active: string | null;
  open: string | null;
  theme: FlowTheme;
  /** Calls that asked about a file Claude had already edited. */
  beforeEdit: ReadonlySet<string>;
  /** Files Claude opened after each Repowise reply named them, by that reply's id. */
  opened: ReadonlyMap<string, string[]>;
}

function lead(a: Activity, ctx: CallContext): Seg[] {
  const mark: Seg = a.id === ctx.active ? { t: `${ACTIVE_MARK} `, color: ctx.theme.amber } : pad(GUTTER);
  return [mark, { t: clockText(a.startedAt - ctx.turn.startedAt).padEnd(CLOCK_W), dim: true }];
}

/** What a reply was built from: quietly, when it predates an edit; then its first facts and how fresh the index was. */
function builtFrom(a: Activity, ctx: CallContext): string[] {
  const reply = a.reply;
  const facts = reply === null ? [] : reply.inside.slice(0, BUILT_FROM).map(insideFact);
  const fresh = reply === null ? null : freshnessText(reply.behind);
  return [...(ctx.beforeEdit.has(a.id) ? [FLOW_COPY.beforeEdit] : []), ...facts, ...(fresh === null ? [] : [fresh])];
}

function rightOf(a: Activity, ctx: CallContext): string {
  if (a.isError) return FLOW_COPY.error;
  if (a.endedAt === null) return FLOW_COPY.asking;
  return [durationText(a.endedAt - a.startedAt), ...builtFrom(a, ctx)].join(SEP);
}

function callLine(a: Activity, ctx: CallContext): Line {
  const what = a.args[0]?.[1] ?? "";
  const sub: Seg[] = a.agentId === null ? [] : [{ t: `  ${FLOW_COPY.subagent}`, dim: true }];
  const segs: Seg[] = [...lead(a, ctx), { t: a.tool, press: rowKey(a.id) }, pad(2), { t: what, flex: true }, ...sub];
  // The target keeps its room; the facts at the right edge give way first.
  const room = ctx.columns - width(segs.filter((s) => s.flex !== true)) - Math.min(what.length, TARGET_MIN) - 2;
  return { node: row(segs, ctx.columns, [{ t: fit(rightOf(a, ctx), Math.max(1, room)), dim: true }]), id: a.id };
}

function labeled(label: string, body: string, ctx: CallContext, id: string): Line {
  return { node: row([pad(DETAIL_INDENT), { t: label.padEnd(DETAIL_W), dim: true }, { t: body, flex: true }], ctx.columns), id };
}

/** Parts joined with ` · `, wrapped onto as many lines as they need, the label on the first. */
function wrapped(label: string, parts: string[], ctx: CallContext, id: string): Line[] {
  const room = Math.max(10, ctx.columns - DETAIL_INDENT - DETAIL_W);
  return packRows(parts, (p) => p.length, room, SEP.length).map((r, i) => labeled(i === 0 ? label : "", r.join(SEP), ctx, id));
}

/** A reply's account of itself and its first characters. */
function replyDetail(a: Activity, ctx: CallContext): Line[] {
  const reply = a.reply;
  if (reply === null) return [];
  const excerpt = reply.excerpt === "" ? [] : [labeled(FLOW_COPY.excerpt, reply.excerpt.replace(/\s+/g, " ").slice(0, EXCERPT_LINE), ctx, a.id)];
  const note = ctx.beforeEdit.has(a.id) ? [FLOW_COPY.beforeEdit] : [];
  return [...wrapped(FLOW_COPY.howAnswered, [...note, ...behindParts(reply.behind, reply.bytes)], ctx, a.id), ...excerpt];
}

/** An open call's detail: how it was answered, its first characters, and what Claude opened after. */
function detailLines(a: Activity, ctx: CallContext): Line[] {
  if (a.id !== ctx.open) return [];
  const opened = ctx.opened.get(a.id) ?? [];
  return [...replyDetail(a, ctx), labeled(FLOW_COPY.thenOpened, opened.length === 0 ? FLOW_COPY.nothingOpened : opened.join(", "), ctx, a.id)];
}

/** Files opened after a reply named them, by the reply's id, in the order opened. */
function openedAfter(state: FlowState): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const a of state.turns.flatMap((t) => t.activities)) {
    const by = a.namedBy?.id;
    const path = a.paths[0];
    if (by === undefined || path === undefined) continue;
    const list = out.get(by) ?? [];
    if (!list.includes(path)) list.push(path);
    out.set(by, list);
  }
  return out;
}

const isCall = (a: Activity): boolean => a.kind === "repowise";

function callLines(state: FlowState, turn: FlowTurn, v: FlowViewInput, theme: FlowTheme): Line[] {
  const ctx: CallContext = {
    turn,
    columns: v.columns,
    active: isWorking(state) ? (inFlight(state)?.id ?? null) : null,
    open: state.open,
    theme,
    beforeEdit: answeredBeforeEdit(state),
    opened: openedAfter(state),
  };
  const calls = turn.activities.filter(isCall).flatMap((a) => [callLine(a, ctx), ...detailLines(a, ctx)]);
  const dropped = turn.dropped > 0 && calls.length > 0 ? [{ node: dimLine(`  ${droppedSteps(turn.dropped)}`, v.columns) }] : [];
  return [...dropped, ...calls];
}

const heightOf = (l: Line): number => l.height ?? 1;
const total = (lines: readonly Line[]): number => lines.reduce((n, l) => n + heightOf(l), 0);

/** Where a window of `room` starts: one line above the open row, else so the newest fit. */
function windowStart(lines: Line[], room: number, open: string | null): number {
  const at = open === null ? -1 : lines.findIndex((l) => l.id === open);
  const floor = at < 0 ? lines.length - 1 : Math.max(0, at - 1);
  let start = 0;
  while (start < floor && total(lines.slice(start)) > room) start++;
  return start;
}

/** Calls in `lines` with no line in `shown`. */
function stepsOutside(lines: Line[], shown: ReadonlySet<string | undefined>): number {
  return new Set(lines.map((l) => l.id).filter((id) => !shown.has(id))).size;
}

/** The lines that fit in `room`, with a count of the calls above, and below, that do not. */
function windowed(lines: Line[], room: number, open: string | null): Line[] {
  if (total(lines) <= room) return lines;
  const start = windowStart(lines, room - 2, open);
  let end = lines.length;
  while (end > start + 1 && total(lines.slice(start, end)) > room - 2) end--;
  const shown = new Set(lines.slice(start, end).map((l) => l.id));
  const above = stepsOutside(lines.slice(0, start), shown);
  const below = stepsOutside(lines.slice(end), shown);
  const note = (n: number, say: (n: number) => string): Line[] => (n === 0 ? [] : [{ node: dimLine(say(n), Number.POSITIVE_INFINITY) }]);
  return [...note(above, hiddenSteps), ...lines.slice(start, end), ...note(below, laterSteps)];
}

// Earlier turns.


/** Earlier turns, newest first, one line each; then the folded ones. */
function earlierTurns(state: FlowState, columns: number): Node[] {
  const out = state.turns
    .slice(0, -1)
    .reverse()
    .map((t) => dimLine(`  ${turnFooter(t.seq, t.durationMs, turnTotals(t), t.end)}`, columns));
  if (state.earlier.turns > 0) out.push(dimLine(`  ${earlierLine(state.earlier)}`, columns));
  return out;
}

function navRow(state: FlowState): Node[] {
  if (openable(state).length === 0) return [];
  return [box({ flexDirection: "row", columnGap: 2 }, [button(FLOW_NEXT, "j", FLOW_COPY.next, true), button(FLOW_PREV, "k", FLOW_COPY.previous, true)])];
}

/** The fixed sections of the latest turn. */
function dashboard(state: FlowState, turn: FlowTurn, v: FlowViewInput, theme: FlowTheme): Node[] {
  return [...beforeAcceptLines(state, turn, v), ...workingLines(state, turn, v, theme), ...contextLines(turn, v.columns, theme)];
}

/** Under a turn that ended with an answer, the hills reflected; quiet otherwise. */
function closing(turn: FlowTurn | undefined, columns: number): Node[] {
  return turn !== undefined && turn.end === "answer" ? [text(loch(columns), { dimColor: true })] : [];
}

export function flowView(state: FlowState, v: FlowViewInput): Node {
  const theme = flowTheme(v.light === true);
  const head = [text(hills(v.columns), { color: theme.plum }), ...header(state, v, theme)];
  const turn = state.turns.at(-1);
  const fixed = turn === undefined ? [] : dashboard(state, turn, v, theme);
  const after = [...closing(turn, v.columns), ...earlierTurns(state, v.columns), ...navRow(state)];
  const room = Math.max(MIN_BODY, v.rows - head.length - fixed.length - after.length - 1);
  const calls = turn === undefined ? [] : windowed(callLines(state, turn, v, theme), room, state.open);
  const callNodes = section(FLOW_COPY.calls, calls.map((l) => l.node));
  return box({ key: FLOW_BOX_ID, flexDirection: "column" }, [...head, ...fixed, ...callNodes, ...after]);
}
