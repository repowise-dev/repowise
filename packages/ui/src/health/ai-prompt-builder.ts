/**
 * Build high-quality AI-agent prompts for the code-health surface and its
 * neighbours (coverage, dead code, doc drift, coupling, conformance, security,
 * hotspots, decisions, commits, refactoring plans, and the file drawer).
 *
 * Every prompt is deliberately structured (role → target → state → tasks →
 * constraints → completion contract) so the agent doesn't have to ask
 * follow-up questions before making its first move.
 *
 * This module is the public surface: hosts, the VS Code extension, and the
 * package's `./health/ai-prompt-builder` export all import from here. Each
 * prompt kind lives in its own module under `./ai-prompts/`, and only the names
 * below are part of the contract; the shared helpers stay internal.
 */

export type { AiPromptFlavor } from "./ai-prompts/shared";

export { buildAiPrompt, type BuildPromptOptions } from "./ai-prompts/refactor-prompt";
export {
  buildCoverageAiPrompt,
  type BuildCoveragePromptOptions,
  type CoverageFilePromptInput,
} from "./ai-prompts/coverage-prompt";
export {
  buildWorkQueueAiPrompt,
  type BuildWorkQueuePromptOptions,
  type WorkQueueItem,
} from "./ai-prompts/work-queue-prompt";
export {
  buildDeadCodeAiPrompt,
  type BuildDeadCodePromptOptions,
  type DeadCodePromptFinding,
} from "./ai-prompts/dead-code-prompt";
export {
  buildDocDriftAiPrompt,
  type BuildDocDriftPromptOptions,
  type DocDriftPromptFinding,
} from "./ai-prompts/doc-drift-prompt";
export {
  buildCouplingAiPrompt,
  type BuildCouplingPromptOptions,
  type CouplingPromptEdge,
  type CouplingPromptNode,
} from "./ai-prompts/coupling-prompt";
export {
  buildConformanceAiPrompt,
  type BuildConformancePromptOptions,
  type ConformancePromptViolation,
} from "./ai-prompts/conformance-prompt";
export {
  buildSecurityAiPrompt,
  type BuildSecurityPromptOptions,
  type SecurityPromptFinding,
} from "./ai-prompts/security-prompt";
export {
  buildHotspotAiPrompt,
  type BuildHotspotPromptOptions,
  type HotspotPromptInput,
} from "./ai-prompts/hotspot-prompt";
export {
  buildDecisionAiPrompt,
  buildDecisionEnforcementAiPrompt,
  type BuildDecisionPromptOptions,
  type DecisionPromptInput,
} from "./ai-prompts/decision-prompts";
export {
  buildCommitAiPrompt,
  type BuildCommitPromptOptions,
  type CommitPromptInput,
} from "./ai-prompts/commit-prompt";
export {
  buildPerformanceOpportunityPrompt,
  buildRefactoringOpportunityPrompt,
  buildRefactoringPlanPrompt,
  type BuildPerformanceOpportunityPromptOptions,
  type BuildRefactoringOpportunityPromptOptions,
  type BuildRefactoringPlanPromptOptions,
} from "./ai-prompts/refactoring-prompts";
export {
  buildFileHealthAiPrompt,
  type BuildFileHealthPromptOptions,
  type FileHealthPromptCategory,
  type FileHealthPromptFinding,
  type FileHealthPromptInput,
  type FileHealthPromptSignals,
} from "./ai-prompts/file-health-prompt";
