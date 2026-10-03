/**
 * Session state and its reducer. Pure: no I/O, no clock, no `$`.
 */

import type { AskReply, AskState, AskTool, PaneTab } from "./ask";
import { mayHaveUsedModel } from "./ask";
import { initialReview, reduceReview, type ChangeRisk, type ReviewAction, type ReviewState } from "./review";

/** `no-repo`: not inside a git work tree, so there is nothing to index and Lens stays quiet. */
export type Mode = "full" | "lite" | "no-index" | "no-cli" | "no-repo";

/** Why a lite session has no map, when it is not simply "no server". */
export type LiteReason = "no-server" | "auth" | "unlisted";

/** One setup hint; each shows at most once per session. */
export type HintKind = "no-server" | "auth" | "unlisted" | "no-index" | "no-cli";

export interface IndexFreshness {
  /** Files changed between the indexed commit and HEAD; null when git could not count them. */
  changedFiles: number | null;
}

/** What the spinner shows for a file. null fields are unknown, not zero. */
export interface FileContext {
  /** Files that import this one or call into it (the server's file-level rollup). */
  callerFiles: number | null;
  contributors: number | null;
}

/** A Read, Edit or Write running on a file inside the indexed repo. */
export interface RunningTool {
  id: string;
  /** Repo-relative, forward slashes. */
  file: string;
}

/**
 * One exception the augment hook flagged on an edited file, as it worded it.
 * Lens reads these; the thresholds and caps are the hook's own.
 */
export type MarginNote =
  | { kind: "decision"; reviewed: boolean; title: string }
  | { kind: "fixes"; count: number; age: string; symbol: string | null };

/**
 * What the savings ledger gained since the session's first snapshot. It
 * covers every agent on this repo: savings events carry a per-connection id,
 * not Claude's session id.
 */
export interface SavingsDelta {
  /** Input tokens saved, measured and inferred together. */
  tokens: number;
  /** The inferred part of `tokens`. */
  inferredTokens: number;
  /** Priced input savings in US dollars; unpriced tokens add nothing here. */
  usd: number;
}

export interface SessionState {
  /** null until the first discovery settles. */
  mode: Mode | null;
  /** Shown while the index is behind HEAD, in full and lite mode only. */
  freshness: IndexFreshness | null;
  /** The hint on screen now. */
  hint: HintKind | null;
  /** Every hint already shown this session, so none comes back. */
  hintsShown: readonly HintKind[];
  /** The indexed repo's root, once discovered. */
  repoRoot: string | null;
  /** The file tool running now, if any. */
  running: RunningTool | null;
  /** File context fetched this session, by repo-relative path. */
  contexts: Readonly<Record<string, FileContext>>;
  /** Margin notes by the Edit or Write call they belong to. */
  notes: Readonly<Record<string, readonly MarginNote[]>>;
  /** Shown only once something was saved; full mode only. */
  savings: SavingsDelta | null;
  /** The last turn's change review. */
  review: ReviewState;
  /** The latest review that came back this session, kept past the turn for the recap and the brief. */
  lastReview: ChangeRisk | null;
  /** Files Claude (or a subagent) wrote this session, repo-relative when inside the repo, each once. */
  touched: readonly string[];
  /** The pane's tab, and the text the Ask field starts with. */
  pane: { tab: PaneTab; draft: string };
  ask: AskState;
  /** Ask replies that may have used the index's own model (see `mayHaveUsedModel`). */
  modelAsks: number;
  /** The context was compacted and the brief not yet sent or passed over. */
  compacted: boolean;
}

export type SessionAction =
  | { type: "discovered"; mode: Mode; liteReason?: LiteReason; freshness: IndexFreshness | null; repoRoot?: string | null }
  | { type: "turnCompleted" }
  | { type: "toolStarted"; tool: RunningTool }
  | { type: "toolEnded"; id: string }
  | { type: "contextLoaded"; file: string; context: FileContext }
  | { type: "notesFor"; id: string; notes: readonly MarginNote[] }
  | { type: "savings"; delta: SavingsDelta }
  | { type: "touched"; path: string }
  | { type: "tab"; tab: PaneTab; draft?: string }
  | { type: "asked"; question: string; tool: AskTool }
  | { type: "answered"; question: string; answer: AskReply }
  | { type: "askFailed"; question: string; tool: AskTool; message: string }
  | { type: "compacted" }
  | { type: "briefDone" }
  | ReviewAction;

export const initialSession: SessionState = {
  mode: null,
  freshness: null,
  hint: null,
  hintsShown: [],
  repoRoot: null,
  running: null,
  contexts: {},
  notes: {},
  savings: null,
  review: initialReview,
  lastReview: null,
  touched: [],
  pane: { tab: "map", draft: "" },
  ask: { phase: "idle" },
  modelAsks: 0,
  compacted: false,
};

export function hintFor(mode: Mode, liteReason: LiteReason | undefined): HintKind | null {
  if (mode === "full" || mode === "no-repo") return null;
  if (mode === "lite") return liteReason ?? "no-server";
  return mode;
}

export function reduce(state: SessionState, action: SessionAction): SessionState {
  switch (action.type) {
    case "discovered": {
      const kind = hintFor(action.mode, action.liteReason);
      const indexed = action.mode === "full" || action.mode === "lite";
      const freshness = indexed ? action.freshness : null;
      const repoRoot = indexed ? (action.repoRoot ?? null) : null;
      // Savings come from the local server: the row leaves with it.
      const savings = action.mode === "full" ? state.savings : null;
      if (kind !== null && !state.hintsShown.includes(kind)) {
        const hintsShown = [...state.hintsShown, kind];
        return { ...state, mode: action.mode, freshness, repoRoot, savings, hint: kind, hintsShown };
      }
      // A hint for a state that no longer holds (the server came up) leaves at once.
      const hint = state.hint === kind ? state.hint : null;
      return { ...state, mode: action.mode, freshness, repoRoot, savings, hint };
    }
    case "turnCompleted":
      // A hint stays up through the first turn that ends while it shows, then retires.
      return state.hint === null ? state : { ...state, hint: null };
    case "toolStarted":
      return { ...state, running: action.tool };
    case "toolEnded":
      // Calls overlap: only the one shown clears the slot.
      return state.running?.id === action.id ? { ...state, running: null } : state;
    case "contextLoaded":
      return { ...state, contexts: { ...state.contexts, [action.file]: action.context } };
    case "notesFor": {
      if (action.notes.length === 0) return state;
      const notes = { ...state.notes, [action.id]: action.notes };
      const decision = action.notes.find((n) => n.kind === "decision");
      const review = decision === undefined ? state.review : reduceReview(state.review, { type: "decisionNoted", title: decision.title });
      return { ...state, notes, review };
    }
    case "savings":
      return { ...state, savings: action.delta.tokens > 0 ? action.delta : null };
    case "touched":
      return state.touched.includes(action.path) ? state : { ...state, touched: [...state.touched, action.path] };
    case "tab":
      return { ...state, pane: { tab: action.tab, draft: action.draft ?? state.pane.draft } };
    case "asked":
      // The field starts empty once its text was asked.
      return { ...state, ask: { phase: "asking", question: action.question, tool: action.tool }, pane: { ...state.pane, draft: "" } };
    case "answered": {
      const modelAsks = state.modelAsks + (mayHaveUsedModel(action.answer) ? 1 : 0);
      return { ...state, modelAsks, ask: { phase: "answered", question: action.question, answer: action.answer } };
    }
    case "askFailed":
      return { ...state, ask: { phase: "failed", question: action.question, tool: action.tool, message: action.message } };
    case "compacted":
      return state.compacted ? state : { ...state, compacted: true };
    case "briefDone":
      return state.compacted ? { ...state, compacted: false } : state;
    default:
      return reduceTurn(state, action);
  }
}

/** The review's actions; a new turn also retires the brief offer, and a landed review is kept for the recap. */
function reduceTurn(state: SessionState, action: ReviewAction): SessionState {
  const review = reduceReview(state.review, action);
  const compacted = action.type === "turnStarted" ? false : state.compacted;
  const lastReview = action.type === "reviewed" ? action.risk : state.lastReview;
  if (review === state.review && compacted === state.compacted && lastReview === state.lastReview) return state;
  return { ...state, review, compacted, lastReview };
}

/** Each decision the augment hook surfaced on an edit this session, once, in the order seen. */
export function surfacedDecisions(state: SessionState): Array<{ title: string; reviewed: boolean }> {
  const seen = new Map<string, boolean>();
  for (const notes of Object.values(state.notes)) {
    for (const n of notes) if (n.kind === "decision" && !seen.has(n.title)) seen.set(n.title, n.reviewed);
  }
  return [...seen].map(([title, reviewed]) => ({ title, reviewed }));
}
