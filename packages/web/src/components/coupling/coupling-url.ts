import { parseAsBoolean, parseAsInteger, parseAsString } from "nuqs";
import {
  ALL_FILE_PAIRS,
  DEFAULT_AREA_FILTERS,
  parseAreaPair,
  serializeAreaPair,
  type AreaPairRef,
  type CouplingAreaFilters,
} from "@repowise-dev/ui/coupling";

/**
 * The Coupling tab's URL state. A value at its default leaves the URL, so a
 * shared link carries only what the reader changed. The Architecture page
 * clears every key here when the reader leaves the tab.
 */
export const couplingUrlParsers = {
  unexplained: parseAsBoolean.withDefault(DEFAULT_AREA_FILTERS.unexplained),
  cross: parseAsBoolean.withDefault(DEFAULT_AREA_FILTERS.crossArea),
  tests: parseAsBoolean.withDefault(DEFAULT_AREA_FILTERS.includeTests),
  min: parseAsInteger.withDefault(DEFAULT_AREA_FILTERS.minSupport),
  recent: parseAsInteger,
  depth: parseAsInteger,
  /** `a|b` for one area pair, `all` for every file pair. */
  pair: parseAsString,
};

type UrlState = {
  unexplained: boolean;
  cross: boolean;
  tests: boolean;
  min: number;
  recent: number | null;
  depth: number | null;
  pair: string | null;
};

export function filtersFromUrl(s: UrlState): CouplingAreaFilters {
  return {
    unexplained: s.unexplained,
    crossArea: s.cross,
    includeTests: s.tests,
    minSupport: Math.max(s.min, 0),
    recentDays: s.recent && s.recent > 0 ? s.recent : null,
    // The route accepts 1-6; a hand-edited link past that falls back to the default.
    depth: s.depth && s.depth > 0 && s.depth <= 6 ? s.depth : null,
  };
}

export function filtersToUrl(f: CouplingAreaFilters): Omit<UrlState, "pair"> {
  return {
    unexplained: f.unexplained,
    cross: f.crossArea,
    tests: f.includeTests,
    min: f.minSupport,
    recent: f.recentDays,
    depth: f.depth,
  };
}

export function selectionFromUrl(raw: string | null): AreaPairRef | typeof ALL_FILE_PAIRS | null {
  return raw === ALL_FILE_PAIRS ? ALL_FILE_PAIRS : parseAreaPair(raw);
}

export function selectionToUrl(sel: AreaPairRef | typeof ALL_FILE_PAIRS | null): string | null {
  if (sel === null) return null;
  return sel === ALL_FILE_PAIRS ? ALL_FILE_PAIRS : serializeAreaPair(sel);
}
