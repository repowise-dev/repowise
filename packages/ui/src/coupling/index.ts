export { CouplingGraph, type CouplingGraphProps } from "./coupling-graph";
export { CouplingTable } from "./coupling-table";

/**
 * The single canonical disclaimer for every change-coupling surface. Co-change
 * is a temporal work-pattern hint drawn from git history, never a verified
 * technical dependency — say it once, reuse it everywhere.
 */
export const COUPLING_DISCLAIMER =
  "Files that tend to change together in the same commit. A temporal hint for hidden relationships, not a verified code dependency.";
