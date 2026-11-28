// ---------------------------------------------------------------------------
// Git Intelligence
// ---------------------------------------------------------------------------

// The canonical shapes, under the names this client has always used for them.
// `Response` is the transport-layer name; the definitions live in
// `@repowise-dev/types/git`.
export type {
  AgentTrend,
  AgentTrendBucket,
  AttributionShareFields,
  Commit as CommitResponse,
  CommitCategory,
  CommitDetail as CommitDetailResponse,
  CommitEvolution,
  CommitEvolutionBucket,
  CommitFile as CommitFileResponse,
  CommitHealth as CommitHealthResponse,
  CommitHealthFinding as CommitHealthFindingResponse,
  CommitStats,
  GitMetadata as GitMetadataResponse,
  GitSummary as GitSummaryResponse,
  Hotspot as HotspotResponse,
  OwnershipEntry,
  ReviewPriority,
  RiskDriver as RiskDriverResponse,
  RiskHistogramBucket,
} from "@repowise-dev/types/git";
