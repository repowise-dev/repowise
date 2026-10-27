/**
 * The shared savings surface.
 *
 * Data and callbacks only: nothing here fetches, routes, reads storage or
 * branches on which host is rendering it. A host maps its response onto
 * `SavingsView`, supplies links through `LinkComponent`, and owns whether the
 * reset notice is showing.
 *
 * Nothing here computes a saving. The server's report service is the only
 * thing that aggregates or prices; these components read fields.
 */
export {
  surfaceLabel,
  agentLabel,
  modelLabel,
  type SavingsView,
  type SpendView,
  type SavingsBreakdownRow,
  type SavingsAgentRow,
  type SavingsOpportunityRow,
} from "./types";
export { SavingsLede, type SavingsLedeProps } from "./savings-lede";
export {
  SavingsSourceTable,
  type SavingsSourceTableProps,
  type SourceRow,
} from "./savings-source-table";
export {
  OpportunityList,
  buildOpportunities,
  type OpportunityListProps,
  type OpportunityItem,
  type BuildOpportunitiesOptions,
} from "./opportunity-list";
export {
  SavingsTimeline,
  MIN_DAYS_FOR_CHART,
  TIMELINE_HEIGHT,
  type SavingsTimelineProps,
} from "./savings-timeline";
export { SpendSummary, type SpendSummaryProps } from "./spend-summary";
export {
  UsageDetails,
  type UsageDetailsProps,
  type UsageDetailTab,
} from "./usage-details";
export {
  SavingsMethodology,
  SavingsResetNotice,
  ACCOUNTING_METHOD_VERSION,
  type SavingsMethodologyProps,
  type SavingsResetNoticeProps,
} from "./savings-methodology";
