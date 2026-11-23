export { CouplingGraph, type CouplingGraphProps } from "./coupling-graph";
export { CouplingTable } from "./coupling-table";
export { CouplingExplorer, type CouplingExplorerProps } from "./coupling-explorer";
export { CouplingAreaView, type CouplingAreaViewProps } from "./area-coupling-view";
export {
  DEFAULT_AREA_COUPLING_COPY,
  type AreaCouplingCopy,
  type AreaCouplingCopyOverrides,
} from "./area-copy";
export {
  ALL_FILE_PAIRS,
  DEFAULT_AREA_FILTERS,
  areaLabel,
  parseAreaPair,
  serializeAreaPair,
  toCouplingFilters,
  type AreaPairRef,
  type CouplingAreaFilters,
} from "./area-view-state";
export {
  couplingIntensityByArea,
  couplingIntensityByFile,
  type CouplingIntensity,
  type CouplingLensOptions,
} from "./lens";
export {
  CouplingPairDrawer,
  type CouplingPairDrawerProps,
} from "./coupling-pair-drawer";
export {
  couplingClaim,
  dependencyKindPhrase,
  isSamePair,
  pairHas,
  peakConfidence,
  segmentOf,
  structuralClause,
  type CouplingPair,
  type CouplingSegment,
} from "./claim";

/**
 * The single canonical disclaimer for every change-coupling surface. Co-change
 * is a temporal work-pattern hint drawn from git history, never a verified
 * technical dependency — say it once, reuse it everywhere.
 */
export const COUPLING_DISCLAIMER =
  "Files that tend to change together in the same commit. A temporal hint for hidden relationships, not a verified code dependency.";
