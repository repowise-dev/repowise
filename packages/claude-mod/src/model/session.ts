/**
 * Session state and its reducer. Pure: no I/O, no clock, no `$`.
 */

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

export interface SessionState {
  /** null until the first discovery settles. */
  mode: Mode | null;
  /** Shown while the index is behind HEAD, in full and lite mode only. */
  freshness: IndexFreshness | null;
  /** The hint on screen now. */
  hint: HintKind | null;
  /** Every hint already shown this session, so none comes back. */
  hintsShown: readonly HintKind[];
}

export type SessionAction =
  | { type: "discovered"; mode: Mode; liteReason?: LiteReason; freshness: IndexFreshness | null }
  | { type: "turnCompleted" };

export const initialSession: SessionState = { mode: null, freshness: null, hint: null, hintsShown: [] };

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
      if (kind !== null && !state.hintsShown.includes(kind)) {
        return { mode: action.mode, freshness, hint: kind, hintsShown: [...state.hintsShown, kind] };
      }
      // A hint for a state that no longer holds (the server came up) leaves at once.
      const hint = state.hint === kind ? state.hint : null;
      return { ...state, mode: action.mode, freshness, hint };
    }
    case "turnCompleted":
      // A hint stays up through the first turn that ends while it shows, then retires.
      return state.hint === null ? state : { ...state, hint: null };
  }
}
