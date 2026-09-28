/**
 * Public surface of the agent-prompt builders. Each prompt kind lives in
 * `./ai-prompts/`; only the names re-exported here are part of the contract.
 * Every prompt runs role, target, state, tasks, constraints, expected output,
 * so the agent can act without asking follow-up questions.
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
