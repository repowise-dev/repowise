/**
 * The shared Usage & savings surface.
 *
 * Data and callbacks only: nothing here fetches, routes, reads storage or
 * branches on which host is rendering it. A host passes the savings report,
 * its spend, links through `LinkComponent`, words through `copy`, and at most
 * one setup nudge.
 *
 * Nothing here computes a saving. The server's report service is the only
 * thing that aggregates or prices; these components read fields and group
 * per-operation rows into plain jobs.
 */
export {
  hasSavings,
  firstFilesDay,
  filesTrackedSince,
  type SavingsView,
  type SavingsBreakdownView,
  type SavingsFilesView,
  type SavingsCheckView,
  type SpendView,
  type SpendRowView,
  type SpendGrouping,
} from "./types";
export {
  DEFAULT_SAVINGS_COPY,
  BENCHMARK_FEWER_FILES_PCT,
  humanizeOperation,
  type SavingsCopy,
} from "./copy";
export {
  SAVINGS_JOB_IDS,
  jobForOperation,
  groupByJob,
  jobValue,
  type SavingsJobId,
  type SavingsJobTotal,
  type SavingsJobMeasure,
} from "./jobs";
export {
  SavingsPageBody,
  SavingsEmpty,
  SavingsNudge,
  type SavingsPageBodyProps,
  type SavingsNudgeProps,
} from "./savings-page";
export { SavingsHero, SavingsProof } from "./savings-hero";
export { SavingsOverTime, type SavingsLens } from "./savings-over-time";
export { SavingsJobs } from "./savings-jobs";
export {
  SavingsChecks,
  CHECK_TOOL_IDS,
  checksHaveOutcomes,
  toolAvailability,
  type CheckToolId,
  type CheckAgentView,
  type SavingsChecksCopy,
  type SavingsChecksProps,
} from "./savings-checks";
export { CHECK_PROMPTS, switchProfilePrompt } from "./check-prompts";
export { RepowiseUsage, USAGE_DAY_LIMIT, type RepowiseUsageProps } from "./repowise-usage";
export {
  SavingsResetNotice,
  ACCOUNTING_METHOD_VERSION,
  type SavingsResetNoticeProps,
} from "./savings-methodology";
