/**
 * Where Claude's attention went this session, and what its last edit reaches.
 * Pure. Paths are kept absolute and normalized (`normalizeRepoPath`), so
 * nothing is lost before discovery has found the repo root; the map view maps
 * them onto its cells.
 */

/**
 * Most recent reads kept for the trail. Ceiling: a session reading more keeps
 * only the newest (the legend then says `200+`); raise it if the trail ever
 * needs the whole session.
 */
const MAX_READS = 200;

export type Callers =
  | { status: "loading" }
  | { status: "ready"; paths: readonly string[] }
  | { status: "failed" };

export interface TrailState {
  /** Files read or edited, oldest first, each once (a re-read keeps its first place). */
  reads: readonly string[];
  /** True once older reads were dropped to stay under the cap. */
  readsCapped: boolean;
  /** Files the latest file-listing Grep or Glob returned. */
  hits: readonly string[];
  /** Bumped per search, so a search that returns the same files flashes again. */
  searches: number;
  /** The latest Edit or Write target. */
  edit: string | null;
  /** Bumped per edit; a callers answer for an older edit is dropped. */
  edits: number;
  /** Files that import `edit`, repo-relative as the server names them. */
  callers: Callers | null;
}

export type TrailAction =
  | { type: "read"; path: string }
  | { type: "search"; paths: readonly string[] }
  | { type: "edit"; path: string }
  | { type: "callers"; edits: number; callers: Callers };

export const initialTrail: TrailState = {
  reads: [],
  readsCapped: false,
  hits: [],
  searches: 0,
  edit: null,
  edits: 0,
  callers: null,
};

function withRead(state: TrailState, path: string): TrailState {
  if (state.reads.includes(path)) return state;
  const reads = [...state.reads, path];
  if (reads.length <= MAX_READS) return { ...state, reads };
  return { ...state, reads: reads.slice(reads.length - MAX_READS), readsCapped: true };
}

export function reduceTrail(state: TrailState, action: TrailAction): TrailState {
  switch (action.type) {
    case "read":
      return withRead(state, action.path);
    case "search":
      return { ...state, hits: action.paths, searches: state.searches + 1 };
    case "edit":
      return {
        ...withRead(state, action.path),
        edit: action.path,
        edits: state.edits + 1,
        callers: { status: "loading" },
      };
    case "callers":
      return action.edits === state.edits ? { ...state, callers: action.callers } : state;
  }
}
