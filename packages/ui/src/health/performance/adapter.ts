import type { CodeHealthAdapter } from "../code-health-adapter";

/**
 * What the Performance tab asks of its host: data and links, nothing about
 * where it is deployed. A host that cannot answer one of the optional members
 * gets an honest fallback, which is why they are narrowed from the canonical
 * adapter rather than redeclared here.
 */
export type PerformanceViewAdapter = Pick<
  CodeHealthAdapter,
  | "cacheKey"
  | "listFindings"
  | "getPerformanceOpportunities"
  | "getPerformanceOpportunity"
  | "getPerformanceOpportunityFindings"
  | "getRefactoringPlan"
  | "refactoringPlanHref"
  | "mapHref"
  | "fileHref"
  | "symbolHref"
  | "navigate"
>;
