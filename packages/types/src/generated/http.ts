// Generated from the FastAPI application's OpenAPI schema. Do not edit.
//
// Regenerate with:  python scripts/generate_http_types.py
// CI fails when this file and the live schema disagree.
//
// Scope: the HTTP boundary only. Artifact, UI and other non-wire domain types
// stay hand-written in the sibling modules.
//
// `?` mirrors the schema's `required` list, which states what a request may
// omit. A response field with a server-side default is still always sent.

/** The provider/model this scope resolves to; ``None`` when unset. */
export interface ActiveProviderSelection {
  provider?: string | null;
  model?: string | null;
}

/** One month of agent-vs-human commit volume. */
export interface AgentTrendBucket {
  month: string;
  total_commits: number;
  agent_commits: number;
  agent_pct: number;
  tier_counts?: Record<string, unknown>;
}

/** Monthly agent-share trend across the indexed commit window. */
export interface AgentTrendResponse {
  buckets: AgentTrendBucket[];
  total_commits: number;
  agent_commits: number;
  agent_pct: number;
  agent_names?: Record<string, unknown>[];
}

export interface ArchEdgeResponse {
  source: string;
  target: string;
  edge_type: string;
  direction: string;
  weight: number;
  confidence: number;
}

export interface ArchLayerResponse {
  id: string;
  name: string;
  description: string;
  node_ids: string[];
  file_count: number;
  complexity_distribution: Record<string, number>;
  health_score: number | null;
  sub_groups?: ArchSubGroupResponse[];
  display_order?: number;
}

export interface ArchNodeResponse {
  id: string;
  node_type: string;
  name: string;
  file_path: string | null;
  line_range: number[] | null;
  summary: string;
  complexity: string;
  tags: string[];
  language: string | null;
  pagerank: number;
  pagerank_percentile: number;
  betweenness: number;
  in_degree: number;
  out_degree: number;
  community_id: number | null;
  is_entry_point: boolean;
  is_test: boolean;
  is_hotspot: boolean;
  is_dead: boolean;
  has_doc: boolean;
  primary_owner: string | null;
  primary_owner_pct: number | null;
  bus_factor: number | null;
}

export interface ArchSubGroupResponse {
  id: string;
  name: string;
  node_ids: string[];
}

export interface ArchTourStepResponse {
  order: number;
  title: string;
  description: string;
  node_ids: string[];
  target_path?: string | null;
  layer_id?: string | null;
  reason?: string;
  depth?: number | null;
  kind?: string;
  page_type?: string | null;
}

export interface ArchitectureEdgeResponse {
  source: number;
  target: number;
  edge_count: number;
}

export interface ArchitectureGraphResponse {
  nodes: ArchitectureNodeResponse[];
  edges: ArchitectureEdgeResponse[];
}

export interface ArchitectureNodeResponse {
  community_id: number;
  label: string;
  cohesion: number;
  member_count: number;
  top_file: string;
  avg_pagerank: number;
  hotspot_count?: number;
  dead_count?: number;
  has_decision?: boolean;
  doc_coverage_pct?: number;
  languages?: string[];
}

export interface ArchitectureViewResponse {
  project_name: string;
  project_description: string;
  layers: ArchLayerResponse[];
  nodes: ArchNodeResponse[];
  edges: ArchEdgeResponse[];
  tour: ArchTourStepResponse[];
  total_files: number;
  total_symbols: number;
  total_edges: number;
  languages: string[];
  frameworks: string[];
  external_systems: C4ExternalSystemResponse[];
  entry_points?: string[];
  entry_candidates?: string[];
}

export interface ArtifactUpdateRequest {
  pinned: boolean;
}

export interface BlastRadiusRequest {
  changed_files: string[];
  max_depth?: number;
}

export interface BlastRadiusResponse {
  direct_risks: DirectRiskEntry[];
  transitive_affected: TransitiveEntry[];
  cochange_warnings: CochangeWarning[];
  recommended_reviewers: ReviewerEntry[];
  test_gaps: string[];
  test_impact: TestImpactResponse;
  structural_impact_score: number;
  structural_impact_band: "localized" | "moderate" | "broad";
  structural_impact_scale: RiskScalarSemantics;
  overall_risk_score: number;
  overall_risk_score_compatibility: RiskCompatibilityField;
}

export interface C4ComponentResponse {
  id: string;
  name: string;
  path: string;
  container_id: string;
  file_count: number;
  symbol_count: number;
}

export interface C4ContainerResponse {
  id: string;
  name: string;
  path: string;
  language: string;
  file_count: number;
  symbol_count: number;
  hotspot_count?: number;
  dead_count?: number;
}

export interface C4ExternalSystemResponse {
  id: string;
  name: string;
  display_name: string;
  category: string;
  ecosystem: string;
  version?: string | null;
  io_kind?: string | null;
}

export interface C4L1Response {
  system: C4SystemResponse;
  people: C4PersonResponse[];
  external_systems: C4ExternalSystemResponse[];
  relations: C4RelationResponse[];
}

export interface C4L2Response {
  containers: C4ContainerResponse[];
  external_systems: C4ExternalSystemResponse[];
  relations: C4RelationResponse[];
}

export interface C4L3Response {
  container: C4ContainerResponse;
  components: C4ComponentResponse[];
  external_systems: C4ExternalSystemResponse[];
  relations: C4RelationResponse[];
}

export interface C4PersonResponse {
  id: string;
  name: string;
  description?: string;
  kind?: string;
}

export interface C4RelationResponse {
  source_id: string;
  target_id: string;
  label?: string;
  edge_count?: number;
  edge_types?: string[];
  coupling?: string;
}

export interface C4SystemResponse {
  id: string;
  name: string;
  description?: string;
}

export interface CallerCalleeEntry {
  symbol_id: string;
  name: string;
  kind: string;
  file: string;
  start_line?: number | null;
  edge_type: string;
  confidence: number;
  resolution_origin?: string | null;
}

export interface CallersCalleesResponse {
  symbol_id: string;
  symbol: SymbolNodeSummary;
  callers: CallerCalleeEntry[];
  callees: CallerCalleeEntry[];
  caller_count: number;
  callee_count: number;
  truncated: boolean;
  relations?: SymbolRelationGroup[];
}

/** The Kamei change-shape features the risk model scores. */
export interface ChangeFeaturesResponse {
  la: number;
  ld: number;
  nf: number;
  nd: number;
  ns: number;
  entropy: number;
  exp: number | null;
}

export interface ChangelogEntryModel {
  version: string;
  label?: string | null;
  sections: ChangelogSectionModel[];
}

export interface ChangelogResponse {
  entries: ChangelogEntryModel[];
}

export interface ChangelogSectionModel {
  name: string;
  items: string[];
}

/**
 * One completed tool call, as stored inside a chat message.
 *
 * ``data`` and ``evidence`` stay open: ``data`` is the raw result of
 * whichever MCP tool ran (a different shape per tool) and ``evidence`` is
 * derived from it, so closing either would turn tool variance into a 500.
 */
export interface ChatArtifactEnvelope {
  id: string;
  version?: number;
  type: string;
  tool_name: string;
  title: string;
  presentation: string;
  data?: Record<string, unknown>;
  evidence?: Record<string, unknown>;
  pinned?: boolean;
  created_at?: string | null;
}

export interface ChatMessageResponse {
  id: string;
  conversation_id: string;
  role: string;
  content: Record<string, unknown>;
  created_at: string;
}

/** Navigation metadata supplied by a product chat surface. */
export interface ChatPageContext {
  kind: "repository" | "overview" | "documentation" | "architecture" | "graph" | "health" | "refactoring" | "file" | "symbol" | "module" | "dependency" | "commit" | "contributor" | "decision" | "risk" | "security" | "usage" | "settings" | "chat";
  label: string;
  target?: string | null;
  target_kind?: "path" | "symbol" | "module" | "commit" | "person" | "decision" | "documentation" | null;
}

export interface ChatRequest {
  message: string;
  conversation_id?: string | null;
  provider?: string | null;
  model?: string | null;
  context?: ChatPageContext | null;
}

/**
 * One file on the churn-vs-complexity scatter.
 *
 * Every figure is coerced by the producer, so none is nullable here: a zero
 * means zero, not "no signal".
 */
export interface ChurnComplexityPoint {
  file_path: string;
  commit_count_90d: number;
  max_ccn: number;
  nloc: number;
  score: number;
  churn_percentile: number;
}

export interface ChurnComplexityResponse {
  points?: ChurnComplexityPoint[];
  total?: number;
}

/** Where the regenerated section landed. */
export interface ClaudeMdGenerateResponse {
  status?: string;
  path: string;
  generated_at: string;
}

/** The Repowise-managed section, rendered but not written to disk. */
export interface ClaudeMdResponse {
  content: string;
  generated_at: string;
  repo_name: string;
  sections?: string[];
}

/**
 * Files that historically change together with one file.
 *
 * Partners are the verbatim persisted records, not a projection: the
 * indexer writes fields this layer does not model, and a closed row model
 * would drop them.
 */
export interface CoChangeResponse {
  file_path: string;
  co_change_partners?: Record<string, unknown>[];
  total?: number;
}

export interface CochangeWarning {
  changed: string;
  missing_partner: string;
  score: number;
}

/** A single commit with its full, attributable risk-driver breakdown. */
export interface CommitDetailResponse {
  sha: string;
  short_sha: string;
  author_name: string;
  author_email: string;
  committed_at: string | null;
  subject: string;
  lines_added: number;
  lines_deleted: number;
  files_changed: number;
  dirs_changed: number;
  subsystems_changed: number;
  entropy: number;
  is_fix: boolean;
  change_risk_score: number | null;
  change_risk_level: string | null;
  risk_percentile: number;
  review_priority: string;
  top_driver?: string | null;
  author_experience?: number | null;
  author_commit_count?: number | null;
  agent_name?: string | null;
  agent_autonomy_tier?: number | null;
  agent_confidence?: string | null;
  drivers?: RiskDriverResponse[];
  agent_channel?: string | null;
}

/**
 * One time bucket of commit-category counts for the Code Evolution timeline.
 *
 * ``counts`` keys are drawn from
 * :data:`repowise.core.ingestion.git_indexer._constants.EVOLUTION_CATEGORIES`;
 * a category is omitted when its count is zero in this bucket.
 */
export interface CommitEvolutionBucket {
  period: string;
  start: string;
  total: number;
  counts?: Record<string, number>;
}

/**
 * Commit-category mix over time — the repo's development "story arc".
 *
 * Each commit is classified into exactly one category (feature/fix/refactor/
 * docs/test/deps/chore/other) from its subject and bucketed by ``granularity``.
 * The UI renders ``buckets`` as a stacked area (share or volume).
 */
export interface CommitEvolutionResponse {
  buckets: CommitEvolutionBucket[];
  categories: string[];
  totals?: Record<string, number>;
  total_commits: number;
  granularity: string;
  first_commit_at?: string | null;
  last_commit_at?: string | null;
}

/**
 * One per-commit row from the ``git_commits`` table.
 *
 * Carries the stored raw change-risk plus a **repo-relative** normalization
 * (``risk_percentile`` + ``review_priority``). The raw ``change_risk_level``
 * is the absolute calibration band — kept for transparency but de-emphasized
 * in the UI, because it skews high on repos with large typical commits. The
 * review-priority queue ranks on ``risk_percentile`` instead.
 */
export interface CommitResponse {
  sha: string;
  short_sha: string;
  author_name: string;
  author_email: string;
  committed_at: string | null;
  subject: string;
  lines_added: number;
  lines_deleted: number;
  files_changed: number;
  dirs_changed: number;
  subsystems_changed: number;
  entropy: number;
  is_fix: boolean;
  change_risk_score: number | null;
  change_risk_level: string | null;
  risk_percentile: number;
  review_priority: string;
  top_driver?: string | null;
  author_experience?: number | null;
  author_commit_count?: number | null;
  agent_name?: string | null;
  agent_autonomy_tier?: number | null;
  agent_confidence?: string | null;
}

/**
 * Repo-wide commit aggregates for the commits-page headline stat cards.
 *
 * Computed over **all** indexed commits, not the loaded page — the paginated
 * feed only returns a window, so client-side reductions over it under-count
 * (e.g. a risk-sorted first page is all top-tercile). These are the honest
 * totals for the whole repository.
 */
export interface CommitStatsResponse {
  total_commits: number;
  high_priority_count: number;
  fix_commit_count: number;
  agent_commit_count: number;
  avg_entropy: number;
  risk_histogram?: RiskHistogramBucket[];
  moderate_cut?: number | null;
  high_cut?: number | null;
}

export interface CommunityDetailResponse {
  community_id: number;
  label: string;
  cohesion: number;
  member_count: number;
  members: CommunityMember[];
  truncated: boolean;
  neighboring_communities: NeighboringCommunity[];
}

export interface CommunityMember {
  path: string;
  pagerank: number;
  is_entry_point: boolean;
}

export interface CommunitySliceNodeResponse {
  node_id: string;
  node_type: string;
  language: string;
  symbol_count: number;
  pagerank: number;
  betweenness: number;
  community_id: number;
  is_test?: boolean;
  is_entry_point?: boolean;
  has_doc?: boolean;
  is_hotspot?: boolean;
  churn_percentile?: number | null;
  is_dead?: boolean;
  dead_confidence?: number | null;
  has_decision?: boolean;
  primary_owner?: string | null;
  is_boundary?: boolean;
}

export interface CommunitySliceResponse {
  nodes: CommunitySliceNodeResponse[];
  links: GraphEdgeResponse[];
  community_id: number;
  member_count: number;
  truncated?: boolean;
}

export interface CommunitySummaryItem {
  community_id: number;
  label: string;
  cohesion: number;
  member_count: number;
  top_file: string;
}

export interface ConversationDetailResponse {
  conversation: ConversationResponse;
  messages?: ChatMessageResponse[];
}

export interface ConversationForkRequest {
  through_message_id?: string | null;
  before_message_id?: string | null;
}

export interface ConversationResponse {
  id: string;
  repository_id: string;
  title: string;
  message_count?: number;
  pinned?: boolean;
  created_at: string;
  updated_at: string;
}

export interface ConversationUpdateRequest {
  title?: string | null;
  pinned?: boolean | null;
}

export interface CoordinatorHealthResponse {
  sql_pages: number | null;
  sql_decisions: number | null;
  vector_count: number | null;
  vector_page_count: number | null;
  vector_decision_count: number | null;
  graph_nodes: number | null;
  drift_pct: number | null;
  page_drift_pct: number | null;
  decision_drift_pct: number | null;
  status: string;
  detail?: string | null;
}

export interface CostGroupResponse {
  group: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

export interface CostSummaryResponse {
  total_cost_usd: number;
  total_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  since: string | null;
}

export interface CouplingEdgeResponse {
  source: string;
  target: string;
  strength: number;
  last_co_change?: string | null;
  support?: number;
  confidence_ab?: number | null;
  confidence_ba?: number | null;
  structural?: "corroborated" | "unexplained" | "not_applicable" | null;
  dependency_kind?: string | null;
}

export interface CouplingGraphResponse {
  nodes?: CouplingNodeResponse[];
  edges?: CouplingEdgeResponse[];
  total_edges?: number;
  coupled_files?: number;
  total_files?: number;
}

export interface CouplingNodeResponse {
  file_path: string;
  module?: string | null;
  score?: number | null;
  nloc?: number;
}

/** 202 launch payload for a re-analysis (an index-only job, no LLM work). */
export interface DeadCodeAnalyzeResponse {
  job_id: string;
  status?: string;
  repository_id: string;
}

export interface DeadCodeFindingResponse {
  id: string;
  kind: string;
  file_path: string;
  symbol_name: string | null;
  symbol_kind: string | null;
  confidence: number;
  reason: string;
  lines: number;
  start_line: number | null;
  end_line: number | null;
  safe_to_delete: boolean;
  risk_factors: string[];
  evidence: string[];
  primary_owner: string | null;
  status: string;
  note: string | null;
  last_commit_at: string | null;
  commit_count_90d: number;
}

export interface DeadCodeGraphNodeResponse {
  node_id: string;
  node_type: string;
  language: string;
  symbol_count: number;
  pagerank: number;
  betweenness: number;
  community_id: number;
  is_test?: boolean;
  is_entry_point?: boolean;
  has_doc?: boolean;
  is_hotspot?: boolean;
  churn_percentile?: number | null;
  is_dead?: boolean;
  dead_confidence?: number | null;
  has_decision?: boolean;
  primary_owner?: string | null;
  confidence_group: string;
}

export interface DeadCodeGraphResponse {
  nodes: DeadCodeGraphNodeResponse[];
  links: GraphEdgeResponse[];
}

export interface DeadCodePatchRequest {
  status: string;
  note?: string | null;
}

export interface DeadCodeSummaryResponse {
  total_findings: number;
  confidence_summary: Record<string, unknown>;
  deletable_lines: number;
  total_lines: number;
  by_kind: Record<string, unknown>;
}

/** A link from a decision to a governed file or module. */
export interface DecisionCodeEdge {
  decision_id: string;
  node_id: string;
  link_type: string;
}

/**
 * Counts by status for a repository, from a grouped COUNT.
 *
 * Exists so a caller can state a total it actually measured. The list
 * endpoint caps at 500 rows, so counting the page reported "97 of 100" on a
 * repository holding several hundred records.
 */
export interface DecisionCountsResponse {
  total: number;
  active: number;
  proposed: number;
  superseded: number;
  deprecated: number;
}

export interface DecisionCreate {
  title: string;
  context?: string;
  decision?: string;
  rationale?: string;
  alternatives?: string[];
  consequences?: string[];
  affected_files?: string[];
  affected_modules?: string[];
  tags?: string[];
}

/** Per-update ceiling on the one broad session-discovery call. */
export interface DecisionDiscoveryBudget {
  max_sessions?: number;
  max_input_tokens?: number;
}

/**
 * A change to the discovery budget. Omitted fields keep their value.
 *
 * Separate from :class:`DecisionDiscoveryBudget` because a response states
 * both numbers while a write may set one, and a shared model would fill the
 * other from its default and quietly reset it.
 */
export interface DecisionDiscoveryPatch {
  max_sessions?: number | null;
  max_input_tokens?: number | null;
}

export interface DecisionEvidenceListResponse {
  evidence?: DecisionEvidenceResponse[];
}

/** One provenance row supporting a decision record. */
export interface DecisionEvidenceResponse {
  id: string;
  source: string;
  source_rank: number;
  evidence_file: string | null;
  evidence_line: number | null;
  evidence_commit: string | null;
  source_quote: string;
  confidence: number;
  verification: string;
  created_at: string;
}

/** A typed directed edge between two decision records. */
export interface DecisionGraphEdge {
  src: string;
  dst: string;
  kind: string;
  confidence: number;
  evidence: string;
}

/** A decision record represented as a graph node. */
export interface DecisionGraphNode {
  id: string;
  title: string;
  status: string;
  source: string;
  confidence: number;
  staleness_score: number;
  verification: string;
}

/** Full decision graph: nodes, decision→decision edges, decision→code edges. */
export interface DecisionGraphResponse {
  nodes: DecisionGraphNode[];
  decision_edges: DecisionGraphEdge[];
  code_edges: DecisionCodeEdge[];
}

/** Governance rollup: what is stale, awaiting review, and ungoverned. */
export interface DecisionHealthResponse {
  summary?: Record<string, number>;
  stale_decisions?: DecisionRecordResponse[];
  proposed_awaiting_review?: DecisionRecordResponse[];
  ungoverned_hotspots?: string[];
}

/**
 * Records per review lane, from a scan of the acceptance join.
 *
 * ``candidates`` and the four currency lanes partition the repository and sum
 * to ``total``; ``governing`` is the roll-up of the two that still bind, so a
 * caller can state "N rules" without adding two tabs together.
 */
export interface DecisionLaneCountsResponse {
  candidates: number;
  active: number;
  needs_review: number;
  uncheckable: number;
  history: number;
  governing: number;
  total: number;
}

/** One node in a decision lineage chain (root → … → current). */
export interface DecisionLineageEntry {
  id: string;
  title: string;
  status: string;
  source: string;
  relation: string | null;
}

/** The supersedes/refines chain, root first. */
export interface DecisionLineageResponse {
  lineage?: DecisionLineageEntry[];
}

export interface DecisionRecordResponse {
  id: string;
  repository_id: string;
  title: string;
  status: string;
  context: string;
  decision: string;
  rationale: string;
  alternatives: string[];
  consequences: string[];
  affected_files: string[];
  affected_modules: string[];
  tags: string[];
  source: string;
  evidence_commits: string[];
  evidence_file: string | null;
  evidence_line: number | null;
  confidence: number;
  staleness_score: number;
  verification?: string;
  scope?: string | null;
  superseded_by: string | null;
  last_code_change: string | null;
  created_at: string;
  updated_at: string;
  evidence_count?: number | null;
  evidence_preview?: EvidencePreview | null;
  currency?: string | null;
}

/** The resolved decision capture policy for one repository. */
export interface DecisionSettings {
  enabled?: boolean;
  llm?: boolean;
  preset?: string;
  discovery?: DecisionDiscoveryBudget;
  sources?: DecisionSourceState[];
  provider_available?: boolean;
  warnings?: string[];
  legacy_keys?: string[];
  etag?: string;
}

/** A partial policy write. Omitted fields keep their current value. */
export interface DecisionSettingsUpdate {
  enabled?: boolean | null;
  llm?: boolean | null;
  preset?: string | null;
  sources?: Record<string, DecisionSourcePatch> | null;
  discovery?: DecisionDiscoveryPatch | null;
  etag?: string | null;
}

/**
 * A change to one source. Omitted fields keep their current value.
 *
 * ``extra="forbid"`` on purpose: an untyped mapping accepted a misspelt
 * ``{"enable": true}`` and returned 200 having changed nothing, so a UI
 * toggle read as saved when it was not.
 */
export interface DecisionSourcePatch {
  enabled?: boolean | null;
  llm?: boolean | null;
}

/** One capture source's capabilities and resolved state. */
export interface DecisionSourceState {
  key: string;
  label: string;
  description: string;
  authority: string;
  deterministic: boolean;
  supports_llm: boolean;
  togglable: boolean;
  enabled: boolean;
  llm_enabled: boolean;
  status: string;
  reason: string;
}

/**
 * PATCH body for /decisions/{id}.
 *
 * All fields are optional — clients can update status alone (the historical
 * contract), the linked modules / files alone (governance editor), or both
 * in a single request. Fields left at ``None`` are preserved.
 */
export interface DecisionStatusUpdate {
  status?: string | null;
  superseded_by?: string | null;
  affected_modules?: string[] | null;
  affected_files?: string[] | null;
}

/**
 * The shortest dependency path between two nodes.
 *
 * ``distance`` is ``-1`` and ``path`` empty when none exists; only then is
 * ``visual_context`` present, carrying the nearest common ancestors and
 * bridge suggestions the UI falls back to.
 */
export interface DependencyPathResponse {
  path?: string[];
  distance: number;
  explanation: string;
  visual_context?: Record<string, unknown> | null;
}

export interface DirectRiskEntry {
  path: string;
  structural_score: number;
  risk_score: number;
  temporal_hotspot: number;
  centrality: number;
}

export interface DistillSavingsGroup {
  group: string;
  events: number;
  raw_tokens: number;
  distilled_tokens: number;
  saved_tokens: number;
}

/**
 * Savings rollup for the Costs page hero card.
 *
 * The ``distill`` block (``saved_tokens`` etc.) covers the ``repowise
 * distill`` command/hook path. The ``mcp`` block surfaces tokens already
 * dropped past MCP response budgets (the ``omissions`` store), which the
 * distill ledger never recorded. Savings are priced at the *coding agent's*
 * detected model (``pricing_model`` / ``pricing_agent`` / ``pricing_source``)
 * — they are input tokens that agent never had to read. ``available`` is
 * False when the repo has no omission store on disk (feature unused).
 */
export interface DistillSavingsResponse {
  available: boolean;
  events?: number;
  raw_tokens?: number;
  distilled_tokens?: number;
  saved_tokens?: number;
  estimated_usd_saved?: number;
  pricing_model?: string;
  pricing_agent?: string;
  pricing_source?: string;
  per_filter?: DistillSavingsGroup[];
  per_day?: DistillSavingsGroup[];
  mcp_events?: number;
  mcp_tokens?: number;
  mcp_queries?: number;
  mcp_per_tool?: McpDropGroup[];
  missed_events?: number;
  missed_tokens_est?: number;
  missed_window_days?: number;
  reread_events?: number;
  reread_tokens_est?: number;
}

export interface EgoGraphResponse {
  nodes: GraphNodeResponse[];
  links: GraphEdgeResponse[];
  center_node_id: string;
  center_git_meta: GitMetadataResponse | null;
  inbound_count: number;
  outbound_count: number;
}

/**
 * Counts by tier and by kind, from a grouped read of the same filters.
 *
 * Exists for the same reason the decision counts endpoint does: a page that
 * counts its own rows reports the size of a window, not the size of a store.
 */
export interface EpisodeCountsResponse {
  available?: boolean;
  total?: number;
  by_tier?: Record<string, number>;
  by_kind?: Record<string, number>;
}

/**
 * One episode, whole, with the currency question actually asked.
 *
 * ``current`` is the gate half of the verdict and ``still_true`` the sentence
 * half. Both are served because they answer different questions: a reader
 * asking *what happened here* wants the sentence even when the scope has
 * moved, while anything putting a claim beside a statement about the present
 * must respect the boolean.
 */
export interface EpisodeDetail {
  id: string;
  tier: string;
  kind: string;
  subject: string;
  body: string;
  evidence: string;
  nodes: string[];
  node_count: number;
  birth_commit: string | null;
  birth_at: string | null;
  last_seen_at: string | null;
  still_true: string;
  current: boolean;
}

/** A page of summaries, with the measured total behind it. */
export interface EpisodeListResponse {
  available?: boolean;
  total?: number;
  episodes?: EpisodeSummary[];
}

/** One timeline row. No body, and no git call was made to build it. */
export interface EpisodeSummary {
  id: string;
  tier: string;
  kind: string;
  subject: string;
  evidence: string;
  nodes: string[];
  node_count: number;
  birth_commit: string | null;
  birth_at: string | null;
  last_seen_at: string | null;
  still_true?: string | null;
}

/** The top-ranked evidence row, slimmed for list rows. */
export interface EvidencePreview {
  source: string;
  source_quote: string;
  verification: string;
  evidence_file?: string | null;
  evidence_line?: number | null;
}

export interface ExecutionFlowEntry {
  entry_point: string;
  entry_point_name: string;
  entry_point_score: number;
  trace: string[];
  depth: number;
  crosses_community: boolean;
  communities_visited: number[];
  termination?: string | null;
  termination_detail?: Record<string, number> | null;
  trace_via?: (string | null)[] | null;
}

export interface ExecutionFlowsResponse {
  total_entry_points: number;
  flows: ExecutionFlowEntry[];
}

/** One declared third-party dependency. */
export interface ExternalSystemEntry {
  name: string;
  display_name: string;
  ecosystem: string;
  category: string;
  io_kind?: string | null;
  version?: string | null;
  declared_in: string;
  is_dev_dep?: boolean;
}

/** One persisted external graph node linked to the selected package. */
export interface ExternalSystemGraphTarget {
  node_id: string;
  match_basis: "exact" | "subpath" | "mapped";
}

export interface ExternalSystemImportingFile {
  path: string;
  language: string;
  import_edge_count: number;
  matched_external_node_count: number;
}

/** One independently bounded page of files behind an aggregate node. */
export interface ExternalSystemImportingFilesResponse {
  package_key: string;
  aggregate_key: string;
  items: ExternalSystemImportingFile[];
  total: number;
  returned: number;
  limit: number;
  offset: number;
  truncated: boolean;
  scope: "primary" | "all";
}

export interface ExternalSystemRelationshipEdge {
  source: string;
  target: string;
  import_edge_count: number;
}

/** Bounded aggregate-first relationship graph for one declared package. */
export interface ExternalSystemRelationshipGraphResponse {
  package_key: string;
  package_name: string;
  package_node_id: string;
  match_basis: "exact" | "subpath" | "mapped" | "mixed" | "unresolved";
  matched_external_nodes: ExternalSystemGraphTarget[];
  matched_external_nodes_total: number;
  matched_external_nodes_truncated: boolean;
  evidence_target_limit: number;
  evidence_truncated: boolean;
  nodes: ExternalSystemRelationshipNode[];
  edges: ExternalSystemRelationshipEdge[];
  aggregate_total: number;
  aggregate_returned: number;
  edge_total: number;
  edge_returned: number;
  importing_file_total: number;
  import_edge_total: number;
  node_limit: number;
  edge_limit: number;
  truncated: boolean;
  scope: "primary" | "all";
}

/** A first-party graph community that imports the selected package. */
export interface ExternalSystemRelationshipNode {
  aggregate_key: string;
  label: string;
  community_id: number;
  importing_file_count: number;
  import_edge_count: number;
  top_file?: string | null;
}

/** One canonical package with declaration and graph-usage aggregates. */
export interface ExternalSystemSummaryEntry {
  package_key: string;
  name: string;
  display_name: string;
  ecosystem: string;
  category: string;
  io_kind?: string | null;
  runtime_declared: boolean;
  dev_declared: boolean;
  declaration_count: number;
  manifest_count: number;
  versions: string[];
  versions_total: number;
  versions_truncated: boolean;
  multiple_versions: boolean;
  external_node_count: number;
  import_edge_count: number;
  importing_file_count: number;
  link_state: "linked" | "unlinked";
}

/** The full dependency registry for a repository. */
export interface ExternalSystemsResponse {
  items: ExternalSystemEntry[];
  total: number;
  prod_count: number;
  dev_count: number;
  ecosystems: string[];
  manifests: string[];
}

/** Bounded package summaries for the external-dependency scan surface. */
export interface ExternalSystemsSummaryResponse {
  items: ExternalSystemSummaryEntry[];
  returned: number;
  total_packages: number;
  limit: number;
  offset: number;
  truncated: boolean;
  scope: "primary" | "all";
  excluded_declarations: number;
  total_declarations: number;
  runtime_packages: number;
  dev_only_packages: number;
  observed_packages: number;
  linked_packages: number;
  unlinked_packages: number;
  linked_without_imports: number;
  ecosystems: string[];
  manifest_count: number;
}

export interface FeedbackRequest {
  /** One of ui_ux, bug, feature_request, other */
  category: string;
  message: string;
  email?: string | null;
  page_url?: string | null;
}

export interface FeedbackResponse {
  ok: boolean;
}

/** One file's score over time. ``points`` is empty on thin history. */
export interface FileHealthTrendResponse {
  file_path: string;
  points?: FileTrendPointResponse[];
  current?: number | null;
  previous?: number | null;
  delta?: number | null;
  unclamped_delta?: number | null;
  declining?: boolean;
  snapshot_count?: number;
}

export interface FileTrendPointResponse {
  taken_at?: string | null;
  score: number;
  unclamped_score: number;
}

export interface FindingStatusUpdate {
  /** open | acknowledged | resolved | false_positive */
  status: string;
}

/** One changed file's recency-weighted bug-fix record. */
export interface FixHistoryFileResponse {
  path: string;
  churn: number;
  fix_pressure: number;
}

/**
 * Bug-fix history of the files a change touches.
 *
 * The size-orthogonal half of the answer: unlike ``score``, none of this grows
 * with the diff. ``density`` is the churn-weighted mean fix pressure of the
 * touched files; ``percentile`` ranks it against the same measure over the
 * repository's own recent commits, and is ``None`` when there is no sample to
 * rank against.
 */
export interface FixHistoryResponse {
  available: boolean;
  density: number;
  percentile: number | null;
  files: FixHistoryFileResponse[];
}

/** Optional per-call overrides for the enrichment provider/model. */
export interface GenerateCodeRequest {
  provider?: string | null;
  model?: string | null;
}

/** Generated refactored code + diff for one plan, with the self-check. */
export interface GenerateCodeResponse {
  suggestion_id?: string | null;
  refactoring_type: string;
  file_path: string;
  target_symbol: string;
  content: string;
  diff: string;
  provider: string;
  model: string;
  cached: boolean;
  input_tokens: number;
  output_tokens: number;
  validation?: Record<string, unknown>;
  spans?: Record<string, unknown>[];
}

/**
 * Body for the generate + estimate endpoints.
 *
 * ``cascade`` is optional: left unset it resolves to ``none`` for a ranked
 * selection (the ranked set is already a coherent slice) and ``dependents`` for
 * an explicit one, matching the CLI ``generate`` defaults.
 */
export interface GenerateRequestBody {
  selection?: GenerateSelectionBody;
  cascade?: "none" | "dependents" | "full" | null;
  style?: string | null;
}

/**
 * Which pages a generate request targets.
 *
 * Two selection philosophies, kept distinct exactly as the CLI keeps them:
 *
 * - **Explicit**: ``all`` / ``unwritten`` / ``stale``, an explicit ``page_ids``
 *   list, or every page under a ``path_prefix`` — the caller names the pages.
 * - **Ranked** (``kind="ranked"``): write the most important slice by the same
 *   importance model ``repowise init`` uses, sized by ``coverage_pct`` (a
 *   fraction in ``(0, 1]``; ``1.0`` == everything) or ``top_n`` (a target page
 *   count, not exact). The two are mutually exclusive.
 *
 * The two philosophies cannot be combined; :func:`_validate_generate_selection`
 * enforces it with an actionable 400.
 */
export interface GenerateSelectionBody {
  kind?: "all" | "unwritten" | "stale" | "page_ids" | "path_prefix" | "ranked";
  page_ids?: string[] | null;
  path_prefix?: string | null;
  coverage_pct?: number | null;
  top_n?: number | null;
}

export interface GitMetadataResponse {
  file_path: string;
  commit_count_total: number;
  commit_count_90d: number;
  commit_count_30d: number;
  first_commit_at: string | null;
  last_commit_at: string | null;
  primary_owner_name: string | null;
  primary_owner_email: string | null;
  primary_owner_commit_pct: number | null;
  recent_owner_name: string | null;
  recent_owner_commit_pct: number | null;
  top_authors: Record<string, unknown>[];
  significant_commits: Record<string, unknown>[];
  co_change_partners: Record<string, unknown>[];
  is_hotspot: boolean;
  is_stable: boolean;
  churn_percentile: number;
  age_days: number;
  bus_factor: number;
  contributor_count: number;
  lines_added_90d: number;
  lines_deleted_90d: number;
  avg_commit_size: number;
  commit_categories: Record<string, unknown>;
  merge_commit_count_90d: number;
  change_entropy?: number;
  change_entropy_pct?: number;
  prior_defect_count?: number;
  fix_symbol_counts?: Record<string, unknown>;
  bug_magnet?: boolean;
  last_fix_at?: string | null;
  temporal_hotspot_score?: number | null;
  commit_count_capped?: boolean;
  original_path?: string | null;
  test_gap?: boolean | null;
  agent_commit_count?: number;
  agent_authored_pct?: number | null;
  agent_tier_counts?: Record<string, unknown>;
}

export interface GitSummaryResponse {
  total_files: number;
  hotspot_count: number;
  stable_count: number;
  average_churn_percentile: number;
  top_owners: Record<string, unknown>[];
}

/** Slim decision reference so lists can render titles, not UUIDs. */
export interface GoverningDecisionRef {
  id: string;
  title: string;
  status: string;
}

export interface GraphEdgeResponse {
  source: string;
  target: string;
  imported_names: string[];
  edge_type?: string | null;
  confidence?: number | null;
}

export interface GraphExportResponse {
  nodes: GraphNodeResponse[];
  links: GraphEdgeResponse[];
  truncated?: boolean;
  total_node_count?: number | null;
  dead_total?: number | null;
  dead_in_view?: number | null;
  hot_total?: number | null;
  hot_in_view?: number | null;
}

export interface GraphMetricsResponse {
  target: string;
  node_type: string;
  pagerank: number;
  pagerank_percentile: number;
  betweenness: number;
  betweenness_percentile: number;
  betweenness_scored?: boolean;
  community_id: number;
  community_label: string | null;
  is_entry_point: boolean;
  in_degree: number;
  out_degree: number;
  entry_point_score?: number | null;
  kind?: string | null;
  file?: string | null;
}

export interface GraphNodeResponse {
  node_id: string;
  node_type: string;
  language: string;
  symbol_count: number;
  pagerank: number;
  betweenness: number;
  community_id: number;
  is_test?: boolean;
  is_entry_point?: boolean;
  has_doc?: boolean;
  is_hotspot?: boolean;
  churn_percentile?: number | null;
  is_dead?: boolean;
  dead_confidence?: number | null;
  has_decision?: boolean;
  primary_owner?: string | null;
}

export interface HTTPValidationError {
  detail?: ValidationError[];
}

/**
 * Shields-compatible badge fields for the JSON endpoint.
 *
 * ``schemaVersion`` is camelCase because the Shields endpoint protocol
 * requires that exact key; without it every embedded badge renders as
 * "invalid response" instead of the score.
 */
export interface HealthBadgeResponse {
  schemaVersion?: number;
  label: string;
  message: string;
  color: string;
  band: string;
}

/** One file's movement between the last two snapshots. */
export interface HealthFileDelta {
  file_path: string;
  before: number;
  after: number;
  delta: number;
}

/** One biomarker finding, as the table and drawer read it. */
export interface HealthFindingResponse {
  id: string;
  file_path: string;
  biomarker_type: string;
  severity: string;
  function_name?: string | null;
  line_start?: number | null;
  line_end?: number | null;
  health_impact: number;
  reason?: string | null;
  details?: Record<string, unknown>;
  status: string;
  dimension?: string;
}

/**
 * A listed finding, resolved to the symbol a tool can look up.
 *
 * ``None`` when the finding is file-level, or no symbol matched its span,
 * so the UI degrades to the file page.
 */
export interface HealthFindingWithSymbolResponse {
  id: string;
  file_path: string;
  biomarker_type: string;
  severity: string;
  function_name?: string | null;
  line_start?: number | null;
  line_end?: number | null;
  health_impact: number;
  reason?: string | null;
  details?: Record<string, unknown>;
  status: string;
  dimension?: string;
  symbol_id?: string | null;
}

export interface HealthResponse {
  status: string;
  db: string;
  version: string;
}

export interface HealthTrendAlert {
  kind: string;
  metric: string;
  current: number;
  baseline?: number | null;
  delta: number;
  message: string;
}

/** One snapshot in the repo-level history, newest first. */
export interface HealthTrendKpiRow {
  taken_at?: string | null;
  hotspot_health: number;
  average_health: number;
  worst_performer_path?: string | null;
  worst_performer_score?: number | null;
}

export interface HealthTrendResponse {
  history?: HealthTrendKpiRow[];
  summary: HealthTrendSummary;
  alerts?: HealthTrendAlert[];
  file_deltas?: HealthFileDelta[];
  file_deltas_total?: number;
  snapshot_count?: number;
}

export interface HealthTrendSummary {
  current_hotspot_health: number;
  current_average_health: number;
  previous_hotspot_health?: number | null;
  previous_average_health?: number | null;
  hotspot_delta?: number | null;
  average_delta?: number | null;
}

/** One file in the triage queue, ranked by impact over effort. */
export interface HealthWorkItem {
  file_path: string;
  score: number;
  nloc: number;
  module?: string | null;
  primary_biomarker: string;
  primary_severity: string;
  primary_reason?: string | null;
  primary_function?: string | null;
  primary_line_start?: number | null;
  primary_line_end?: number | null;
  primary_suggestion?: string | null;
  primary_finding_id: string;
  total_impact: number;
  finding_count: number;
  biomarkers?: string[];
  effort_bucket: string;
  impact_per_effort: number;
}

export interface HealthWorkQueueResponse {
  targets?: HealthWorkItem[];
  total?: number;
}

export interface HotFilesGraphResponse {
  nodes: HotFilesNodeResponse[];
  links: GraphEdgeResponse[];
}

export interface HotFilesNodeResponse {
  node_id: string;
  node_type: string;
  language: string;
  symbol_count: number;
  pagerank: number;
  betweenness: number;
  community_id: number;
  is_test?: boolean;
  is_entry_point?: boolean;
  has_doc?: boolean;
  is_hotspot?: boolean;
  churn_percentile?: number | null;
  is_dead?: boolean;
  dead_confidence?: number | null;
  has_decision?: boolean;
  primary_owner?: string | null;
  commit_count: number;
}

export interface HotspotResponse {
  file_path: string;
  commit_count_total?: number;
  commit_count_90d: number;
  commit_count_30d: number;
  churn_percentile: number;
  temporal_hotspot_score?: number | null;
  primary_owner: string | null;
  primary_owner_commit_pct?: number | null;
  recent_owner_name?: string | null;
  recent_owner_commit_pct?: number | null;
  is_hotspot: boolean;
  is_stable: boolean;
  bus_factor: number;
  contributor_count: number;
  lines_added_90d: number;
  lines_deleted_90d: number;
  avg_commit_size: number;
  commit_categories: Record<string, unknown>;
  merge_commit_count_90d?: number;
  commit_count_capped?: boolean;
  age_days?: number;
  last_commit_at?: string | null;
  change_entropy?: number;
  change_entropy_pct?: number;
  prior_defect_count?: number;
  bug_magnet?: boolean;
  last_fix_at?: string | null;
  original_path?: string | null;
}

/**
 * The 202 payload every background-job launch returns.
 *
 * ``stream_token`` authorizes ``/api/jobs/{job_id}/stream`` directly, so a
 * client can attach without a second round-trip or an API key in the query
 * string.
 */
export interface JobAcceptedResponse {
  job_id: string;
  status?: string;
  stream_token: string;
}

export interface JobResponse {
  id: string;
  repository_id: string;
  status: string;
  provider_name: string;
  model_name: string;
  total_pages: number;
  completed_pages: number;
  failed_pages: number;
  current_level: number;
  error_message: string | null;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  stream_token?: string | null;
}

export interface KnowledgeMapOwner {
  email: string;
  name: string;
  files_owned: number;
  percentage: number;
}

export interface KnowledgeMapResponse {
  top_owners: KnowledgeMapOwner[];
  knowledge_silos: KnowledgeMapSilo[];
  onboarding_targets: KnowledgeMapTarget[];
}

export interface KnowledgeMapSilo {
  file_path: string;
  owner_email: string;
  owner_pct: number;
}

export interface KnowledgeMapTarget {
  path: string;
  pagerank: number;
  doc_words: number;
}

/**
 * Per-tool MCP savings (``tool`` with the ``mcp:`` prefix stripped).
 *
 * ``kind`` distinguishes a ``"counterfactual"`` saving (the answer replaced raw
 * file exploration — recorded in the savings ledger) from a ``"truncation"``
 * drop (content trimmed past the response budget — the only signal for tools
 * without a counterfactual estimator yet).
 */
export interface McpDropGroup {
  tool: string;
  events: number;
  tokens: number;
  kind?: string;
}

/** One tool in the configurable surface, with the flags a UI needs. */
export interface McpToolInfo {
  name: string;
  description: string;
  tier: string;
  default: boolean;
  default_single_repo: boolean;
  default_workspace: boolean;
  eligible: boolean;
  eligible_single_repo: boolean;
  eligible_workspace: boolean;
  requires_workspace: boolean;
  enabled: boolean;
  recipes?: McpToolRecipeInfo[];
  artifact_type: string;
  presentation: string;
  safety: string;
  evidence_basis: string;
}

export interface McpToolRecipeInfo {
  name: string;
  call: string;
  requires: string[];
}

/** The full tool surface for a repo plus its current override. */
export interface McpToolSurfaceResponse {
  repo_id?: string | null;
  is_workspace: boolean;
  override?: string[] | string | null;
  tools: McpToolInfo[];
}

export interface ModuleEdgeResponse {
  source: string;
  target: string;
  edge_count: number;
}

export interface ModuleGraphResponse {
  nodes: ModuleNodeResponse[];
  edges: ModuleEdgeResponse[];
}

/** Single-module deep view with breakdowns. */
export interface ModuleHealthDetail {
  module_path: string;
  file_count: number;
  symbol_count: number;
  hotspot_count: number;
  dead_code_count: number;
  dead_code_lines: number;
  avg_churn_percentile: number;
  median_bus_factor: number;
  min_bus_factor: number;
  primary_owner: string | null;
  primary_owner_pct: number;
  is_silo: boolean;
  decision_count: number;
  doc_coverage_pct: number;
  health_score: number;
  owners: ModuleHealthOwner[];
  top_hotspots: string[];
  governing_decisions: GoverningDecisionRef[];
  contributor_count: number;
}

export interface ModuleHealthOwner {
  name: string;
  email: string | null;
  file_count: number;
  pct: number;
}

/** One row in the per-module health rollup. */
export interface ModuleHealthSummary {
  module_path: string;
  file_count: number;
  symbol_count: number;
  hotspot_count: number;
  dead_code_count: number;
  dead_code_lines: number;
  avg_churn_percentile: number;
  median_bus_factor: number;
  min_bus_factor: number;
  primary_owner: string | null;
  primary_owner_pct: number;
  is_silo: boolean;
  decision_count: number;
  doc_coverage_pct: number;
  health_score: number;
}

export interface ModuleNodeResponse {
  module_id: string;
  file_count: number;
  symbol_count: number;
  avg_pagerank: number;
  doc_coverage_pct: number;
  hotspot_count?: number;
  dead_count?: number;
  has_decision?: boolean;
  primary_owner?: string | null;
}

export interface NeighboringCommunity {
  community_id: number;
  label: string;
  cross_edge_count: number;
}

export interface NodeSearchResult {
  node_id: string;
  language: string;
  symbol_count: number;
}

/** Acknowledgement for a mutation with nothing else to report. */
export interface OkResponse {
  ok?: boolean;
}

/**
 * How much coding-agent activity lands on the files this person owns.
 *
 * Aggregated from the per-file agent-provenance rollup (GitMetadata) over
 * the owner's primary-owned files — agent identity per commit lives on the
 * commits surface, the per-file rollup only carries counts and tiers.
 */
export interface OwnerAgentCollab {
  files_with_agent_commits: number;
  agent_commit_count: number;
  agent_share_pct: number | null;
  tier_counts?: Record<string, unknown>;
}

export interface OwnerCoAuthor {
  name: string;
  email: string | null;
  shared_files: number;
  co_change_strength: number;
}

export interface OwnerFileEntry {
  file_path: string;
  commit_count_90d: number;
  churn_percentile: number;
  bus_factor: number;
  is_hotspot: boolean;
  last_commit_at: string | null;
  primary_owner_commit_pct: number | null;
}

/** One row in the engineering-leader-facing owners directory. */
export interface OwnerListEntry {
  key: string;
  name: string;
  email: string | null;
  files_owned: number;
  hotspots_owned: number;
  silo_modules: number;
  dead_code_files_owned: number;
  dead_code_lines_owned: number;
  commit_count_90d: number;
  last_commit_at: string | null;
  bus_factor_risk_files: number;
}

export interface OwnerModuleRollup {
  module_path: string;
  file_count: number;
  hotspot_count: number;
  dominant_pct: number;
}

export interface OwnerProfileResponse {
  key: string;
  name: string;
  email: string | null;
  files_owned: number;
  hotspots_owned: number;
  silo_modules: number;
  dead_code_files_owned: number;
  dead_code_lines_owned: number;
  commit_count_90d: number;
  last_commit_at: string | null;
  first_commit_at: string | null;
  bus_factor_risk_files: number;
  lines_added_90d_est: number;
  lines_deleted_90d_est: number;
  modules: OwnerModuleRollup[];
  top_files: OwnerFileEntry[];
  files_touched_total?: number;
  co_authors: OwnerCoAuthor[];
  co_authors_total?: number;
  commit_categories: Record<string, unknown>;
  agent_collab?: OwnerAgentCollab | null;
}

export interface OwnershipEntry {
  module_path: string;
  primary_owner: string | null;
  owner_pct: number | null;
  file_count: number;
  is_silo: boolean;
}

/** PATCH body for /lookup/notes. ``None`` clears the note. */
export interface PageNotesUpdate {
  human_notes?: string | null;
}

export interface PageResponse {
  id: string;
  repository_id: string;
  page_type: string;
  title: string;
  target_path: string;
  source_hash: string;
  model_name: string;
  provider_name: string;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  generation_level: number;
  version: number;
  confidence: number;
  freshness_status: string;
  content_chars: number;
  layer_id?: string | null;
  layer_name?: string | null;
  is_chapter?: boolean;
  human_notes?: string | null;
  parent_page_id?: string | null;
  display_order?: number;
  section_number?: string | null;
  structural_key?: string | null;
  created_at: string;
  updated_at: string;
  content: string;
  metadata: Record<string, unknown>;
}

export interface PageVersionResponse {
  id: string;
  page_id: string;
  version: number;
  page_type: string;
  title: string;
  content: string;
  source_hash: string;
  model_name: string;
  provider_name: string;
  input_tokens: number;
  output_tokens: number;
  confidence: number;
  archived_at: string;
}

export interface Paginated_CommitResponse_ {
  items: CommitResponse[];
  total: number;
  has_more: boolean;
  next_offset?: number | null;
}

export interface Paginated_HotspotResponse_ {
  items: HotspotResponse[];
  total: number;
  has_more: boolean;
  next_offset?: number | null;
}

export interface Paginated_ModuleHealthSummary_ {
  items: ModuleHealthSummary[];
  total: number;
  has_more: boolean;
  next_offset?: number | null;
}

export interface Paginated_OwnerListEntry_ {
  items: OwnerListEntry[];
  total: number;
  has_more: boolean;
  next_offset?: number | null;
}

export interface Paginated_OwnershipEntry_ {
  items: OwnershipEntry[];
  total: number;
  has_more: boolean;
  next_offset?: number | null;
}

export interface Paginated_SymbolResponse_ {
  items: SymbolResponse[];
  total: number;
  has_more: boolean;
  next_offset?: number | null;
}

/** One provider in the catalog, as the settings picker renders it. */
export interface ProviderEntry {
  id: string;
  name: string;
  models?: string[];
  default_model?: string | null;
  configured?: boolean;
}

export interface ProviderStatusResponse {
  active: ActiveProviderSelection;
  providers?: ProviderEntry[];
}

/**
 * Outcome of the live single-provider smoke test.
 *
 * A failed probe reports ``ok=False`` with ``error`` set rather than raising,
 * so the settings UI renders a clean error state.
 */
export interface ProviderValidationResponse {
  ok: boolean;
  provider?: string | null;
  model?: string | null;
  error?: string | null;
}

/** One page of composed opportunities, with facets and the rollup. */
export interface RefactoringOpportunitiesResponse {
  items?: Record<string, unknown>[];
  total?: number;
  offset?: number;
  has_more?: boolean;
  next_offset?: number | null;
  facets?: Record<string, Record<string, number>>;
  summary?: Record<string, unknown> | null;
  ignored_arguments?: Record<string, string> | null;
}

/**
 * One opportunity: its ordered steps, evidence, validation and plans.
 *
 * ``extra="allow"``: the base row is composed in core and this response
 * spreads an evidence block over it, so the declared keys are the stable
 * part and anything else passes through rather than being dropped.
 */
export interface RefactoringOpportunityDetailResponse {
  resolved: boolean;
  steps?: Record<string, unknown>[];
  steps_total?: number;
  steps_emitted?: number;
  steps_reduced_reason?: string | null;
  steps_next_cursor?: number | null;
  validation_profiles?: Record<string, unknown>[];
  affected_files?: string[];
  lead_finding_ids?: string[];
  next_actions?: Record<string, unknown>[];
  plans?: Record<string, unknown>[] | null;
  ordering_note?: string | null;
}

/** What one opportunity-level triage decision wrote. */
export interface RefactoringOpportunityStatusResponse {
  opportunity_id: string;
  status: string;
  steps_updated: number;
  status_changed_at?: string | null;
}

/** The finding-triage vocabulary, applied to a whole opportunity. */
export interface RefactoringOpportunityStatusUpdate {
  /** open | acknowledged | resolved | false_positive */
  status: string;
}

/** Bounded product page; the legacy targets response remains unpaged. */
export interface RefactoringPlanPageResponse {
  items: RefactoringPlanResponse[];
  total: number;
  has_more: boolean;
  next_offset: number | null;
  summary: RefactoringSummary;
  structural_leads: RefactoringPlanResponse[];
}

/**
 * One ranked refactoring plan, with its open ``plan`` / ``evidence`` /
 * ``blast_radius`` dicts re-hydrated from the persisted ``*_json`` columns.
 */
export interface RefactoringPlanResponse {
  id: string;
  refactoring_type: string;
  file_path: string;
  target_symbol: string;
  line_start?: number | null;
  line_end?: number | null;
  plan?: Record<string, unknown>;
  evidence?: Record<string, unknown>;
  impact_delta?: number;
  effort_bucket?: string;
  blast_radius?: Record<string, unknown>;
  confidence?: string;
  source_biomarker?: string;
  benefit?: number;
  leverage?: number;
  cost?: number;
  risk?: number;
  rank_score?: number;
  dependents?: number;
  file_nloc?: number;
  file_weighted_deficit?: number;
  validation?: Record<string, unknown>;
}

/** What one plan-level triage decision wrote. */
export interface RefactoringPlanStatusResponse {
  id: string;
  public_id?: string | null;
  status: string;
  status_reason?: string | null;
  status_changed_at?: string | null;
}

/** The repository rollup and its one lead. */
export interface RefactoringRollupResponse {
  summary?: Record<string, unknown>;
  directive?: Record<string, unknown>;
}

/** The opt-in code-generation switches, mirrored from ``refactoring.llm``. */
export interface RefactoringSettings {
  enabled?: boolean;
  provider?: string | null;
  model?: string | null;
}

/** Same shape and vocabulary as health finding triage — one triage system. */
export interface RefactoringStatusUpdate {
  /** open | acknowledged | resolved | false_positive */
  status: string;
}

export interface RefactoringSummary {
  total: number;
  by_type: RefactoringTypeCount[];
  files_total?: number | null;
  structural_total?: number | null;
  performance_total?: number | null;
  small_effort_total?: number | null;
  health_recovery_total?: number | null;
  negligible_health_total?: number | null;
  best_health_gain?: number | null;
}

export interface RefactoringTargetsResponse {
  summary: RefactoringSummary;
  plans: RefactoringPlanResponse[];
}

export interface RefactoringTypeCount {
  type: string;
  count: number;
}

export interface RepoCreate {
  name: string;
  local_path: string;
  url?: string;
  default_branch?: string;
  settings?: Record<string, unknown> | null;
  index?: boolean;
}

/** What a repository delete removed, for the confirmation toast. */
export interface RepoDeletedResponse {
  ok?: boolean;
  deleted_pages?: number;
}

export interface RepoResponse {
  id: string;
  name: string;
  url: string;
  local_path: string;
  default_branch: string;
  head_commit: string | null;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  workspace_alias?: string | null;
  workspace_status?: string | null;
  is_primary?: boolean | null;
  docs_enabled?: boolean | null;
  docs_mode?: "none" | "deterministic" | "llm" | null;
  docs_skip_reason?: string | null;
  run_mode?: string | null;
  git_tier?: string | null;
  initial_job_id?: string | null;
}

export interface RepoStatsResponse {
  file_count: number;
  symbol_count: number;
  entry_point_count: number;
  doc_coverage_pct: number;
  freshness_score: number;
  dead_export_count: number;
}

/**
 * One repository's headline figures, for the multi-repo dashboard.
 *
 * Every count here is a count of the thing its name says. ``file_count`` in
 * particular is file nodes only: ``/stats`` counts every ``graph_nodes`` row,
 * which on this repo is 38,813 against 3,600 actual files, because symbol
 * nodes live in the same table.
 */
export interface RepoSummaryRow {
  id: string;
  name: string;
  local_path: string;
  updated_at?: string | null;
  status?: string;
  file_count?: number;
  symbol_count?: number;
  entry_point_count?: number;
  doc_page_count?: number;
  doc_fresh_page_count?: number;
  dead_export_count?: number;
  tracked_file_count?: number;
  hotspot_count?: number;
  average_health?: number | null;
  hotspot_health?: number | null;
  health_taken_at?: string | null;
  indexed_commit?: string | null;
  live_head?: string | null;
  index_behind?: boolean | null;
}

export interface RepoUpdate {
  name?: string | null;
  url?: string | null;
  default_branch?: string | null;
  settings?: Record<string, unknown> | null;
}

export interface ReposSummaryResponse {
  repos: RepoSummaryRow[];
}

export interface ReviewerEntry {
  email: string;
  files: number;
  ownership_pct: number;
}

export interface ReviewerSuggestion {
  name: string;
  email: string | null;
  score: number;
  recent_commits: number;
  owned_paths: string[];
  co_change_paths: string[];
  reasons: string[];
}

export interface ReviewerSuggestionsResponse {
  paths: string[];
  suggestions: ReviewerSuggestion[];
}

export interface RiskAuthority {
  authoritative_for: string;
  primary_fields: string[];
  primary_basis: string;
  fallback_field: string;
  fallback_basis: string;
  score_role: string;
}

export interface RiskCalibration {
  status: string;
  source?: string | null;
  calibrated_at?: string | null;
  population?: string | null;
  granularity?: string | null;
}

export interface RiskCompatibilityField {
  deprecated: boolean;
  replacement: string;
  equivalent_value: boolean;
  historical_meaning: string;
}

/** One feature's signed contribution to a commit's change-risk logit. */
export interface RiskDriverResponse {
  feature: string;
  value: number | null;
  contribution: number;
  label: string;
}

/** One bin of the repo's supporting 0-10 diff-shape score distribution. */
export interface RiskHistogramBucket {
  start: number;
  end: number;
  count: number;
}

/**
 * Change-risk report for a live ``base..head`` git range.
 *
 * Scored on demand from the working tree rather than the indexed commit
 * table, so it works for ranges that haven't been indexed yet (an open PR
 * branch). Mirrors ``repowise risk <base>..<head> --format json`` field for
 * field.
 */
export interface RiskRangeResponse {
  base: string;
  head: string;
  fix_history: FixHistoryResponse;
  risk_authority: RiskAuthority;
  score: number;
  score_measures: string;
  score_unit: string;
  risk_percentile: number | null;
  review_priority: string | null;
  classification: string | null;
  fallback_band: string | null;
  is_fix: boolean;
  features: ChangeFeaturesResponse;
  drivers: RiskDriverResponse[];
}

export interface RiskScalarSemantics {
  field: string;
  kind: string;
  unit: string;
  range: RiskScaleRange | null;
  measures: string;
  deterministic?: boolean;
  calibration?: RiskCalibration | null;
  authoritative?: boolean | null;
  authoritative_for_change_review?: boolean | null;
  runtime_breakage_probability?: boolean | null;
  formula?: string | null;
  thresholds?: Record<string, number> | null;
  band_thresholds?: Record<string, number> | null;
  component_fields?: Record<string, Record<string, unknown>> | null;
}

export interface RiskScaleRange {
  minimum: number | null;
  maximum: number | null;
}

export interface SearchResultResponse {
  page_id: string;
  title: string;
  page_type: string;
  target_path: string;
  score: number;
  snippet: string;
  search_type: string;
}

export interface SecurityFindingResponse {
  id: number;
  file_path: string;
  kind: string;
  severity: string;
  snippet: string | null;
  detected_at: string;
  line_number: number | null;
  line_verified: boolean;
  commit_sha: string | null;
  commit_at: string | null;
  found_in_history: boolean;
}

export interface SetActiveProviderRequest {
  provider: string;
  model?: string | null;
  repo_id?: string | null;
}

export interface SetApiKeyRequest {
  api_key: string;
  repo_id?: string | null;
}

/**
 * Transparent breakdown of the composite importance score so the UI can
 * explain *why* a symbol ranks where it does. All fields are normalized to
 * [0, 1] except booleans.
 */
export interface SymbolImportanceComponents {
  file_pagerank?: number;
  visibility_factor?: number;
  complexity_norm?: number;
  kind_boost?: number;
  is_entry_point?: boolean;
}

export interface SymbolNodeSummary {
  symbol_id: string;
  name: string;
  kind: string;
  file: string;
  start_line?: number | null;
  signature?: string | null;
}

/**
 * One relation kind, on one side of a symbol, with its true total.
 *
 * `total` is counted unbounded while `rows` is capped, so a surface can say
 * "12 of 1,516" instead of silently rendering the cap as the count.
 */
export interface SymbolRelationGroup {
  direction: "in" | "out";
  edge_type: string;
  group: string;
  total: number;
  rows: CallerCalleeEntry[];
}

export interface SymbolResponse {
  id: string;
  repository_id: string;
  file_path: string;
  symbol_id: string;
  name: string;
  qualified_name: string;
  kind: string;
  signature: string;
  start_line: number;
  end_line: number;
  docstring: string | null;
  visibility: string;
  is_async: boolean;
  complexity_estimate: number;
  language: string;
  parent_name: string | null;
  importance_score?: number | null;
  importance_components?: SymbolImportanceComponents | null;
  file_pagerank?: number | null;
  is_entry_point?: boolean | null;
  file_churn_percentile?: number | null;
  file_is_hotspot?: boolean | null;
  blame_mod_count?: number | null;
  blame_recent_mod_count?: number | null;
  blame_median_author_time?: number | null;
  blame_owner_name?: string | null;
  blame_owner_line_pct?: number | null;
  fix_count?: number | null;
}

export interface TestImpactAnalysis {
  status: "available" | "partial" | "degraded";
  stale: boolean;
  partial: boolean;
  degraded: boolean;
  basis_categories: ("measured" | "inferred")[];
}

export interface TestImpactCoverage {
  status: "available" | "partial" | "unavailable" | "degraded";
  reason?: string | null;
  map_present: boolean;
  pair_count: number;
  test_count: number;
  source_file_count: number;
  changed_files_total: number;
  changed_files_with_measured_tests: number;
  changed_files_without_measured_tests: number;
  ingested_at?: string | null;
  source_format?: string | null;
  freshness: TestImpactFreshness;
}

export interface TestImpactEvidence {
  basis: "measured" | "inferred";
  source_file: string;
  via: "coverage-map" | "call-graph" | "import-graph";
  source_format?: string | null;
}

export interface TestImpactFile {
  source_file: string;
  status: "measured" | "inferred" | "unknown";
  measured_tests: string[];
  measured_tests_total: number;
  inferred_tests: string[];
  inferred_tests_total: number;
}

export interface TestImpactFreshness {
  status: "current" | "stale" | "unknown";
  reason?: string | null;
  ingested_commit?: string | null;
  indexed_commit?: string | null;
}

export interface TestImpactInference {
  status: "available" | "degraded";
  reason?: string | null;
  changed_files_total: number;
  changed_files_with_candidates: number;
  candidates_before_dedup: number;
}

export interface TestImpactResponse {
  recommendations: TestRecommendation[];
  recommendations_total: number;
  recommendations_emitted: number;
  recommendations_truncated: boolean;
  recommendations_omitted: number;
  recommendations_by_primary_basis: Record<string, number>;
  files: TestImpactFile[];
  files_total: number;
  files_without_measured_tests: string[];
  unknown_files: string[];
  coverage: TestImpactCoverage;
  inference: TestImpactInference;
  analysis: TestImpactAnalysis;
}

export interface TestRecommendation {
  test_id: string;
  test_file?: string | null;
  repository_id: string;
  repository: string;
  basis: "measured" | "inferred";
  bases: ("measured" | "inferred")[];
  source_files: string[];
  evidence: TestImpactEvidence[];
}

export interface TransitiveEntry {
  path: string;
  depth: number;
}

/**
 * Persist a new ``mcp.tools`` override for a repo.
 *
 * ``tools`` accepts the same shapes as the config block: a list of explicit
 * names or ``+``/``-`` deltas, the string ``"all"``, or ``null``/empty to
 * clear the override and fall back to the default surface.
 */
export interface UpdateMcpToolsRequest {
  repo_id: string;
  tools?: string[] | string | null;
}

export interface ValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}

/** Server version, PyPI freshness, and (optional) per-repo store status. */
export interface VersionResponse {
  server_version: string;
  latest_version?: string | null;
  update_available?: boolean | null;
  upgrade_command: string;
  store_format_version?: number | null;
  store_compatible?: boolean | null;
  reindex_recommended?: boolean;
  reindex_command?: string | null;
}

export interface WebhookResponse {
  event_id: string;
  status?: string;
}

/** Architecture-complexity metrics over the system graph (Phase 6). */
export interface WorkspaceArchitectureResponse {
  node_count?: number;
  structural_edge_count?: number;
  propagation_cost?: number;
  propagation_cost_pct?: number;
  core_size?: number;
  core_ratio?: number;
  core_members?: string[];
  cycle_count?: number;
  conformance_violations?: number;
  architecture_type?: string;
  score?: number;
  role_breakdown?: Record<string, number>;
  roles?: WorkspaceNodeArchitectureRole[];
  generated_at?: string;
}

export interface WorkspaceBlastRadiusResponse {
  targets?: string[];
  target_repos?: string[];
  impacted?: WorkspaceImpactedNode[];
  impacted_repos?: string[];
  structural_count?: number;
  behavioral_count?: number;
  max_distance?: number;
  total_impacted?: number;
  unresolved_targets?: string[];
}

export interface WorkspaceBreakingChange {
  kind: string;
  severity: string;
  contract_id: string;
  contract_type: string;
  provider_repo: string;
  provider_file: string;
  provider_symbol: string;
  provider_symbol_id?: string | null;
  provider_service?: string | null;
  provider_node_id?: string;
  detail: string;
  field_name?: string | null;
  old_value?: string | null;
  new_value?: string | null;
  impacted_consumers?: WorkspaceImpactedConsumer[];
}

export interface WorkspaceBreakingChangesResponse {
  version?: number;
  generated_at?: string | null;
  changes?: WorkspaceBreakingChange[];
  total?: number;
  breaking_count?: number;
  warning_count?: number;
  impacted_repos?: string[];
  impacted_services?: string[];
  total_impacted_consumers?: number;
}

export interface WorkspaceCoChangeEntry {
  source_repo: string;
  source_file: string;
  target_repo: string;
  target_file: string;
  strength: number;
  frequency: number;
  last_date: string;
}

export interface WorkspaceCoChangesResponse {
  co_changes: WorkspaceCoChangeEntry[];
  total: number;
  total_mined?: number;
}

export interface WorkspaceConformanceResponse {
  version?: number;
  generated_at?: string | null;
  rules_evaluated?: number;
  violations?: WorkspaceConformanceViolation[];
  cycles?: WorkspaceDependencyCycle[];
  violation_count?: number;
  cycle_count?: number;
  total_cycles?: number;
  violating_repos?: string[];
}

export interface WorkspaceConformanceViolation {
  rule_source: string;
  rule_target: string;
  rule_description?: string;
  source: string;
  source_name: string;
  target: string;
  target_name: string;
  edge_id: string;
  edge_kind: string;
  severity?: string;
}

/**
 * One contract, keyed by ``(repo, file_path, contract_id)``.
 *
 * Carries the request/response shape that the list endpoint deliberately
 * withholds: ``schema`` is present on roughly a third of contracts and single
 * rows run to full inline type declarations, so it is affordable one at a time
 * and not 200 at a time.
 */
export interface WorkspaceContractDetail {
  contract: WorkspaceContractEntry;
  contract_schema?: Record<string, unknown> | null;
  links?: WorkspaceContractLinkEntry[];
  unmatched_reason?: string | null;
}

export interface WorkspaceContractEntry {
  contract_id: string;
  contract_type: string;
  role: string;
  repo: string;
  file_path: string;
  symbol_name: string;
  confidence: number;
  service?: string | null;
  line?: number | null;
  symbol_id?: string | null;
  meta?: Record<string, unknown>;
}

export interface WorkspaceContractLinkEntry {
  contract_id: string;
  contract_type: string;
  match_type: string;
  confidence: number;
  provider_repo: string;
  provider_file: string;
  provider_symbol: string;
  consumer_repo: string;
  consumer_file: string;
  consumer_symbol: string;
  provider_service?: string | null;
  consumer_service?: string | null;
  provider_symbol_id?: string | null;
  consumer_symbol_id?: string | null;
}

export interface WorkspaceContractSummary {
  total_contracts?: number;
  total_links?: number;
  by_type?: Record<string, number>;
}

export interface WorkspaceContractsResponse {
  contracts: WorkspaceContractEntry[];
  links: WorkspaceContractLinkEntry[];
  total_contracts: number;
  total_links: number;
  by_type?: Record<string, number>;
}

export interface WorkspaceCrossRepoSummary {
  co_change_count?: number;
  package_dep_count?: number;
  top_connections?: Record<string, unknown>[];
}

export interface WorkspaceDependencyCycle {
  nodes?: string[];
  edge_ids?: string[];
  length?: number;
}

export interface WorkspaceExtractionDiagnostics {
  total_providers?: number;
  total_consumers?: number;
  total_links?: number;
  weak_link_count?: number;
  repo_breakdown?: WorkspaceRepoDiagnostics[];
  unmatched_consumers?: WorkspaceUnmatchedConsumer[];
  unmatched_by_reason?: Record<string, number>;
  orphan_providers?: WorkspaceOrphanProvider[];
  providers_by_layer?: Record<string, number>;
  consumers_by_layer?: Record<string, number>;
  http_consumers_unresolved?: number;
  http_consumer_coverage?: number | null;
}

export interface WorkspaceGraphEdge {
  source: string;
  target: string;
  type: string;
  strength?: number;
  label?: string | null;
}

export interface WorkspaceGraphNode {
  repo_id: string;
  name: string;
  file_count?: number;
  coverage_pct?: number;
  health_score?: number;
  health_score_source?: string;
  top_language?: string;
}

export interface WorkspaceGraphResponse {
  nodes?: WorkspaceGraphNode[];
  edges?: WorkspaceGraphEdge[];
}

export interface WorkspaceImpactedConsumer {
  repo: string;
  service?: string | null;
  node_id: string;
  file: string;
  symbol: string;
  symbol_id?: string | null;
  match_type?: string;
  confidence?: number;
}

export interface WorkspaceImpactedNode {
  id: string;
  repo: string;
  name: string;
  kind?: string;
  distance: number;
  score: number;
  structural: boolean;
  edge_kinds?: string[];
}

export interface WorkspaceNodeArchitectureRole {
  id: string;
  repo?: string;
  name?: string;
  visibility_fan_in?: number;
  visibility_fan_out?: number;
  role?: string;
}

export interface WorkspaceOrphanProvider {
  repo: string;
  file_path: string;
  contract_id: string;
  contract_type: string;
}

export interface WorkspaceRepoDiagnostics {
  repo: string;
  providers_by_type?: Record<string, number>;
  consumers_by_type?: Record<string, number>;
  provider_count?: number;
  consumer_count?: number;
  providers_by_layer?: Record<string, number>;
  consumers_by_layer?: Record<string, number>;
  http_consumers_unresolved?: number;
}

export interface WorkspaceRepoEntry {
  alias: string;
  path: string;
  is_primary?: boolean;
  indexed_at?: string | null;
  last_commit_at_index?: string | null;
  repo_id?: string | null;
  file_count?: number;
  symbol_count?: number;
  page_count?: number;
  doc_coverage_pct?: number;
  hotspot_count?: number;
  health_score?: number | null;
  status?: string;
  docs_enabled?: boolean;
  docs_skip_reason?: string | null;
}

export interface WorkspaceResponse {
  is_workspace: boolean;
  workspace_root?: string | null;
  workspace_name?: string | null;
  repos?: WorkspaceRepoEntry[];
  default_repo?: string | null;
  cross_repo_summary?: WorkspaceCrossRepoSummary | null;
  contract_summary?: WorkspaceContractSummary | null;
}

export interface WorkspaceSyncResponse {
  results: WorkspaceSyncResult[];
  accepted?: number;
  skipped?: number;
  errors?: number;
}

export interface WorkspaceSyncResult {
  alias: string;
  job_id?: string | null;
  repo_id?: string | null;
  status: string;
  reason?: string | null;
}

export interface WorkspaceSystemEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
  match_type: string;
  confidence?: number;
  weight?: number;
  structural?: boolean;
  contract_refs?: string[];
}

export interface WorkspaceSystemGraphResponse {
  version?: number;
  generated_at?: string;
  nodes?: WorkspaceSystemNode[];
  edges?: WorkspaceSystemEdge[];
  diagnostics?: WorkspaceExtractionDiagnostics;
}

export interface WorkspaceSystemNode {
  id: string;
  repo: string;
  service_path?: string | null;
  name: string;
  kind?: string;
  provider_count?: number;
  consumer_count?: number;
  contract_types?: string[];
  is_orphan_provider?: boolean;
  is_orphan_consumer?: boolean;
  is_isolated?: boolean;
}

export interface WorkspaceUnmatchedConsumer {
  repo: string;
  file_path: string;
  contract_id: string;
  contract_type: string;
  reason: string;
}

export interface ZoomMapResponse {
  root_id: string;
  project_name: string;
  total_files: number;
  unclaimed_files?: number;
  max_depth: number;
  truncated?: boolean;
  nodes: ZoomNodeResponse[];
  relations: ZoomRelationResponse[];
}

export interface ZoomMetricsResponse {
  file_count?: number;
  descendant_count?: number;
  hotspot_count?: number;
  dead_count?: number;
  entry_point_count?: number;
  on_flow_count?: number;
}

export interface ZoomNodeResponse {
  id: string;
  parent_id?: string | null;
  level: number;
  kind: string;
  name: string;
  path?: string;
  children?: string[];
  importance?: number;
  sibling_rank?: number;
  metrics?: ZoomMetricsResponse;
  summary?: string;
  language?: string | null;
  page_id?: string;
  health_score?: number | null;
  is_entry_point?: boolean;
  is_hotspot?: boolean;
  is_dead?: boolean;
  is_test?: boolean;
  on_flow?: boolean;
}

export interface ZoomRelationResponse {
  parent_id: string;
  source_id: string;
  target_id: string;
  label?: string;
  edge_count?: number;
  coupling?: string;
}
