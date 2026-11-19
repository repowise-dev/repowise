/**
 * The area coupling view's state, as plain data a host can keep in its URL.
 *
 * Defaults describe the view a reader opens on: area pairs across areas, test
 * files left out (a test is expected to change with its code). Pure, so the
 * host's URL parsers and the filter bar agree on one shape.
 */
import type { CouplingFilters } from "@repowise-dev/types/coupling";

export interface CouplingAreaFilters {
  /** Only file pairs no dependency explains. */
  unexplained: boolean;
  /** Only file pairs spanning two areas. */
  crossArea: boolean;
  /** Keep pairs touching test material. */
  includeTests: boolean;
  /** Shared commits a pair needs; 0 keeps every stored pair. */
  minSupport: number;
  /** Days back from the newest co-change in the index; `null` for all history. */
  recentDays: number | null;
  /** Area depth; `null` lets the server choose. */
  depth: number | null;
}

export const DEFAULT_AREA_FILTERS: CouplingAreaFilters = {
  unexplained: false,
  crossArea: true,
  includeTests: false,
  minSupport: 0,
  recentDays: null,
  depth: null,
};

/** The wire filters both coupling routes take, rooted at *root*. */
export function toCouplingFilters(f: CouplingAreaFilters, root = ""): CouplingFilters {
  return {
    root,
    unexplained: f.unexplained,
    crossArea: f.crossArea,
    excludeTests: !f.includeTests,
    ...(f.minSupport > 0 ? { minSupport: f.minSupport } : {}),
    ...(f.recentDays ? { recentDays: f.recentDays } : {}),
    ...(f.depth ? { depth: f.depth } : {}),
  };
}

/** One area pair, the drill-down's subject. */
export interface AreaPairRef {
  source: string;
  target: string;
}

/** The URL value that opens every file pair instead of one area pair. */
export const ALL_FILE_PAIRS = "all";

const PAIR_SEP = "|";

export function serializeAreaPair(pair: AreaPairRef): string {
  return `${pair.source}${PAIR_SEP}${pair.target}`;
}

/** An area pair from its URL value; `null` for anything that is not one. */
export function parseAreaPair(raw: string | null | undefined): AreaPairRef | null {
  if (!raw || !raw.includes(PAIR_SEP)) return null;
  const [source, target, ...rest] = raw.split(PAIR_SEP);
  return rest.length === 0 ? { source: source ?? "", target: target ?? "" } : null;
}

/** An area's display name: its path with a trailing slash, or the root's label. */
export function areaLabel(path: string, rootLabel: string): string {
  return path ? `${path}/` : rootLabel;
}
