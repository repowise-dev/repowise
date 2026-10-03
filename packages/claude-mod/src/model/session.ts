/**
 * Session state and its reducer. Pure: no I/O, no clock, no `$`.
 */

import { initialReview, reduceReview, type ReviewAction, type ReviewState } from "./review";

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
}

export type SessionAction =
  | { type: "discovered"; mode: Mode; liteReason?: LiteReason; freshness: IndexFreshness | null; repoRoot?: string | null }
  | { type: "turnCompleted" }
  | { type: "toolStarted"; tool: RunningTool }
  | { type: "toolEnded"; id: string }
  | { type: "contextLoaded"; file: string; context: FileContext }
  | { type: "notesFor"; id: string; notes: readonly MarginNote[] }
  | { type: "savings"; delta: SavingsDelta }
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
    case "notesFor":
      return action.notes.length === 0 ? state : { ...state, notes: { ...state.notes, [action.id]: action.notes } };
    case "savings":
      return { ...state, savings: action.delta.tokens > 0 ? action.delta : null };
    default: {
      const review = reduceReview(state.review, action);
      return review === state.review ? state : { ...state, review };
    }
  }
}
