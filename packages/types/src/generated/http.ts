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

export interface ActionCommand {
  purpose: string;
  mcp?: string | null;
  cli?: string | null;
  tool?: string | null;
  arguments?: Record<string, unknown> | null;
}

export interface ActionContext {
  production_files: number;
  active_authors_90d: number;
  fix_commits_90d: number;
  busy_threshold: number;
  coverage: "measured" | "stale" | "unknown";
  /** The index holds too few commits for history-driven rules to rank by. */
  history_too_short?: boolean;
}

export interface ActionDetail {
  path: string;
  line?: number | null;
  symbol?: string | null;
  marker?: string | null;
  severity?: string | null;
  reason?: string;
  ref?: string | null;
}

export interface ActionHorizon {
  actions: NextAction[];
  /** Every visible action in this horizon, not only those listed. */
  total: number;
  /** Actions the person dismissed, snoozed or marked done. */
  hidden: number;
  by_tier: Record<string, number>;
}

export interface ActionRuleStatus {
  rule: string;
  status: "evaluated" | "not_applicable" | "unavailable";
  reason: string;
  emitted: number;
}

export interface ActionStateRequest {
  /** None clears the person's answer. */
  state: "dismissed" | "snoozed" | "done" | null;
  fingerprint?: string;
  snooze_days?: number;
}

export interface ActionStateResponse {
  action_id: string;
  state: "dismissed" | "snoozed" | "done" | null;
  until?: string | null;
}

export interface ActionTarget {
  kind: "file" | "symbol" | "folder" | "document" | "decision" | "repo";
  path: string;
  symbol?: string | null;
}

export interface ActionWhy {
  label: string;
  value: string;
  basis: "measured" | "inferred" | "unknown";
}

export interface ActionsResponse {
  status: "available";
  /** Newest indexed commit time; windows count back from it. */
  anchor: string | null;
  week_start: string | null;
  context: ActionContext;
  horizons: Record<string, ActionHorizon>;
  /** Where things stand in each horizon, in one sentence. */
  summary: Record<string, string>;
  rules: ActionRuleStatus[];
  unavailable: Record<string, string>;
}

/** The provider/model this scope resolves to; ``None`` when unset. */
export interface ActiveProviderSelection {
  provider?: string | null;
  model?: string | null;
  base_url?: string | null;
  provenance?: SelectionProvenance;
  base_url_unconfirmed?: boolean;
  input_cost_per_1k?: number | null;
  output_cost_per_1k?: number | null;
  embedder?: string | null;
}

/** What the index knows about one affected file. ``None`` means unknown, never healthy. */
export interface AffectedSignal {
  path: string;
  is_hotspot?: boolean | null;
  health_score?: number | null;
  module?: string | null;
  is_test?: boolean;
  indexed?: boolean | null;
}

export interface AgentPromptResponse {
  flavor: "generic" | "claude-code" | "claude-code-mcp" | "cursor";
  text: string;
  preview?: string | null;
}

/** One month of commits by attribution label. */
export interface AgentTrendBucket {
  total_commits?: number;
  agent_commits?: number;
  human_attested_commits?: number;
  unknown_commits?: number;
  coverage_pct?: number | null;
  agent_pct_of_covered?: number | null;
  suppressed_reason?: string | null;
  month: string;
  tier_counts?: Record<string, unknown>;
}

/**
 * Monthly attribution trend across the indexed commit window.
 *
 * ``enabled`` is false, with every figure empty, when AI attribution is off
 * for the repository.
 */
export interface AgentTrendResponse {
  total_commits?: number;
  agent_commits?: number;
  human_attested_commits?: number;
  unknown_commits?: number;
  coverage_pct?: number | null;
  agent_pct_of_covered?: number | null;
  suppressed_reason?: string | null;
  enabled?: boolean;
  coverage_floor_pct: number;
  buckets?: AgentTrendBucket[];
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
  population?: PopulationBreakdown | null;
  unclustered?: UnclusteredFiles | null;
}

export interface ArchitectureNodeResponse {
  community_id: number;
  label: string;
  cohesion: number;
  conductance?: number | null;
  member_count: number;
  hidden_member_count?: number;
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

export interface AreaBandCounts {
  at_risk: number;
  needs_work: number;
  fair: number;
  good: number;
  excellent: number;
}

export interface AreaBandShare {
  files: number;
  nloc: number;
  pct: number;
}

export interface AreaBandShares {
  at_risk: AreaBandShare;
  needs_work: AreaBandShare;
  fair: AreaBandShare;
  good: AreaBandShare;
  excellent: AreaBandShare;
}

/** The snapshots behind ``health_delta_30d``; the baseline may be nearer than 30 days. */
export interface AreaDeltaWindow {
  target_days: number;
  latest_at: string | null;
  baseline_at: string | null;
}

export interface AreaDetailResponse {
  area: AreaNode;
  distribution: AreaDistribution;
  worst_files: AreaFileRow[];
  hotspots: AreaHotspots;
  owners: AreaOwners;
  trend: AreaTrendPoint[];
  top_findings: AreaFinding[];
  test_reach: AreaTestReach;
  delta_window: AreaDeltaWindow;
  coupled_areas?: CouplingAreaSummary[];
}

export interface AreaDistribution {
  total_files: number;
  total_nloc: number;
  bands: AreaBandShares;
}

export interface AreaFileRow {
  path: string;
  score: number;
  band: "at_risk" | "needs_work" | "fair" | "good" | "excellent";
  nloc: number;
}

export interface AreaFinding {
  id: string;
  file_path: string;
  biomarker_type: string;
  severity: string;
  function_name: string | null;
  line_start: number | null;
  health_impact: number | null;
  reason: string | null;
}

/** The origins the map holds out by default, and what this answer left out. */
export interface AreaHeld {
  default: string[];
  excluded: string[];
  counts: Record<string, number>;
}

export interface AreaHotspots {
  total: number;
  files: AreaFileRow[];
}

export interface AreaNode {
  path: string;
  name: string;
  depth: number;
  parent: string | null;
  has_children: boolean;
  direct_file_count: number;
  file_count: number;
  nloc: number;
  health: number;
  band: "at_risk" | "needs_work" | "fair" | "good" | "excellent";
  band_counts: AreaBandCounts;
  worst_file: AreaWorstFile;
  hotspot_count: number;
  bus_factor_1_count: number;
  primary_owner: AreaOwner | null;
  line_coverage_pct: number | null;
  coverage_file_count: number;
  findings: AreaSeverityCounts;
  health_delta_30d: number | null;
}

export interface AreaOwner {
  key: string;
  name: string;
  kind: "human" | "agent" | "bot";
  files: number;
  share: number;
}

export interface AreaOwners {
  total: number;
  top: AreaOwner[];
}

export interface AreaSeverityCounts {
  critical: number;
  high: number;
  medium: number;
  low: number;
}

export interface AreaTestReach {
  files: number;
  with_test_file: number;
  measured_files: number;
  line_coverage_pct: number | null;
}

export interface AreaTreeResponse {
  root: AreaNode;
  depth: number;
  nodes: AreaNode[];
  delta_window: AreaDeltaWindow;
  held?: AreaHeld | null;
}

export interface AreaTrendPoint {
  taken_at: string | null;
  health: number;
}

export interface AreaWorstFile {
  path: string | null;
  score: number;
}

export interface ArtifactUpdateRequest {
  pinned: boolean;
}

export interface BlastRadiusRequest {
  changed_files: string[];
  max_depth?: number;
  with_graph?: boolean;
}

export interface BlastRadiusResponse {
  affected_signals?: AffectedSignal[] | null;
  changed_owners?: ChangedOwner[] | null;
  test_commands?: string[] | null;
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

/** An entity a card or view points at. Never a URL; the renderer resolves it. */
export interface CardRef {
  type: "repo" | "file" | "symbol" | "module" | "component" | "commit" | "change" | "decision";
  id: string;
  start_line?: number | null;
  end_line?: number | null;
}

export interface ChangeAuthor {
  name: string;
  email: string;
}

export interface ChangeBlastRadiusRequest {
  change_id: string;
  max_depth?: number;
}

export interface ChangeBlastRadiusResponse {
  affected_signals?: AffectedSignal[] | null;
  changed_owners?: ChangedOwner[] | null;
  test_commands?: string[] | null;
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
  change_id: string;
  changed_files: string[];
  changed_files_total: number;
}

export interface ChangeCandidateModel {
  id: string;
  kind: "working_tree" | "branch" | "commit" | "pull_request";
  label: string;
  title?: string | null;
  author?: string | null;
  committed_at?: string | null;
  head_sha?: string | null;
  base?: string | null;
  ahead?: number | null;
  files_changed?: number | null;
  is_current?: boolean;
  pushed?: boolean | null;
  number?: number | null;
  base_sha?: string | null;
  pr_review?: PrReviewSummary | null;
}

/** The changes a reviewer can pick, and the one to open first. */
export interface ChangeCandidatesResponse {
  changes: ChangeCandidateModel[];
  default_id?: string | null;
  working_tree_supported: boolean;
  health_check_supported?: boolean;
}

/** The evidence checklist (``repowise.core.changes.evidence``), versioned. */
export interface ChangeEvidence {
  schema_version: number;
  change_id?: string | null;
  revspec?: string | null;
  generated_at: string;
  index_commit?: string | null;
  items: EvidenceItem[];
}

/** Status counts of a change's kept checklist; never builds one. */
export interface ChangeEvidenceSummary {
  generated_at: string;
  ok: number;
  attention: number;
  unknown: number;
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

export interface ChangeFile {
  path: string;
  added: number;
  deleted: number;
}

/** Forge facts from a metadata source; ``None`` means unknown. */
export interface ChangeForgeMetadata {
  source?: string | null;
  reviewers?: string[] | null;
  approvals?: number | null;
  labels?: string[] | null;
  linked_issues?: string[] | null;
}

export interface ChangeHealthCheckRequest {
  change_id: string;
}

/**
 * ``repowise health check <revspec> --format json`` for one listed change.
 *
 * ``findings`` are the gate's rows (health findings introduced, worsened or
 * fixed, and the diff-risk rules); ``gate`` holds the verdict at the CLI
 * defaults (fail on introduced, at high).
 */
export interface ChangeHealthCheckResponse {
  change_id: string;
  revspec: string;
  status: string;
  explanation?: string | null;
  scope: Record<string, unknown>;
  skipped: Record<string, string>;
  findings: Record<string, unknown>[];
  gate: Record<string, unknown>;
}

/** A finding a change's commit introduced or worsened, with that commit. */
export interface ChangeHealthFindingResponse {
  change_kind: string;
  dimension: string;
  biomarker_type: string;
  severity: string;
  severity_before?: string | null;
  path: string;
  symbol?: string | null;
  line_start?: number | null;
  line_end?: number | null;
  attribution_basis: string;
  reason: string;
  moved_from?: MovedFromResponse | null;
  sha: string;
}

/**
 * A change's health rollup plus its worse findings, worst first and
 * capped: file, lines, symbol and biomarker, enough for a fix prompt.
 */
export interface ChangeHealthResponse {
  status: string;
  kind: "improved" | "worsened" | "mixed" | "neutral";
  scanned_commits: number;
  unscanned_commits: number;
  introduced: number;
  worsened: number;
  resolved: number;
  improved?: number | null;
  moved?: number | null;
  files_improved?: number | null;
  files_worsened: number;
  findings?: ChangeHealthFindingResponse[];
}

/** ``pr`` | ``change_id`` | ``merge`` | ``commit``, first that applies. */
export interface ChangeIdentity {
  kind: string;
  value: string;
}

export interface ChangeListItem {
  schema_version: number;
  id: string;
  identity: ChangeIdentity;
  shape: string;
  forge: string;
  forge_ref?: string | null;
  title: string;
  merged_at?: string | null;
  head_sha: string;
  author: ChangeAuthor;
  commit_count: number;
  files_changed: number;
  lines_added: number;
  lines_deleted: number;
  category: string;
  provenance?: ChangeProvenance | null;
  health?: HealthRollupResponse | null;
  touches_hotspot?: number;
  evidence_summary?: ChangeEvidenceSummary | null;
}

export interface ChangeListResponse {
  items: ChangeListItem[];
  total: number;
  limit: number;
  offset: number;
  attribution: boolean;
  checklists_built?: ChecklistsBuilt | null;
}

/** Three-state AI provenance over the change's commits, with its coverage. */
export interface ChangeProvenance {
  label: string;
  agent_commits: number;
  human_attested_commits: number;
  unknown_commits: number;
  coverage?: number | null;
}

export interface ChangeRecord {
  schema_version: number;
  id: string;
  identity: ChangeIdentity;
  shape: string;
  forge: string;
  forge_ref?: string | null;
  title: string;
  merged_at?: string | null;
  head_sha: string;
  author: ChangeAuthor;
  commit_count: number;
  files_changed: number;
  lines_added: number;
  lines_deleted: number;
  category: string;
  provenance?: ChangeProvenance | null;
  health?: ChangeHealthResponse | null;
  touches_hotspot?: number;
  commits: string[];
  files: ChangeFile[];
  metadata: ChangeForgeMetadata;
  evidence?: ChangeEvidence | null;
  evidence_building?: boolean;
}

/** Who owns some of the changed files, by primary authorship. */
export interface ChangedOwner {
  email: string;
  name?: string | null;
  files: number;
}

export interface ChangelogEntryModel {
  version: string;
  label?: string | null;
  summary?: string;
  highlights?: ChangelogHighlightModel[];
  stats?: ReleaseStatsModel | null;
  sections: ChangelogSectionModel[];
}

export interface ChangelogHighlightModel {
  id: string;
  title: string;
  blurb: string;
  href?: string | null;
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

/** A file a tool result cited, and that result's ``artifact_id``. */
export interface ChatCitation {
  file_path: string;
  symbol_name?: string | null;
  start_line?: number | null;
  end_line?: number | null;
  artifact_id?: string | null;
  ref?: CardRef | null;
}

/**
 * A number the answer states. Offsets are UTF-16 units into ``answer``;
 * a verified figure carries the ``artifact_id`` of the result holding it.
 */
export interface ChatFigure {
  text: string;
  start: number;
  end: number;
  verified: boolean;
  artifact_id?: string | null;
  derived?: boolean;
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
  kind: "repository" | "overview" | "documentation" | "architecture" | "graph" | "health" | "refactoring" | "file" | "symbol" | "module" | "dependency" | "commit" | "contributor" | "decision" | "risk" | "dead-code" | "blast-radius" | "security" | "usage" | "settings" | "chat";
  label: string;
  target?: string | null;
  target_kind?: "path" | "symbol" | "module" | "dependency" | "commit" | "person" | "decision" | "documentation" | null;
}

export interface ChatRequest {
  message: string;
  conversation_id?: string | null;
  provider?: string | null;
  model?: string | null;
  context?: ChatPageContext | null;
}

export interface ChatSessionMessageRequest {
  message: string;
  idempotency_key?: string | null;
  context?: ChatPageContext | null;
  stream?: boolean;
  provider?: string | null;
  model?: string | null;
}

/** Open a session, or resume the one ``(namespace, external_ref)`` names. */
export interface ChatSessionOpenRequest {
  repo_id: string;
  scope?: "repo" | "workspace";
  namespace?: string;
  external_ref?: string | null;
  title?: string | null;
  policy?: ChatSessionPolicyRequest | null;
}

/**
 * Limits a caller asks for. Each may only narrow the server's default
 * session policy; a wider value is refused.
 */
export interface ChatSessionPolicyRequest {
  tool_allowlist?: string[] | null;
  max_tool_loops?: number | null;
  max_turns?: number | null;
  max_output_tokens?: number | null;
  max_turn_tokens?: number | null;
  max_session_tokens?: number | null;
  audience?: "engineer" | "business" | null;
}

export interface ChatSessionResponse {
  id: string;
  repository_id: string;
  title: string;
  scope: "repo" | "workspace";
  namespace?: string | null;
  external_ref?: string | null;
  created?: boolean;
  policy?: Record<string, unknown>;
  turns?: number;
  input_tokens?: number;
  output_tokens?: number;
}

/**
 * One composer chip. ``source`` lets a client rank a measured question
 * above the static tier it already ships.
 */
export interface ChatSuggestion {
  text: string;
  source: "static" | "page" | "followup";
  toolHint?: string | null;
}

/**
 * Only the measured tier. An empty list means the page had nothing to
 * measure, and the client's own static tier stands.
 */
export interface ChatSuggestionsResponse {
  suggestions?: ChatSuggestion[];
}

export interface ChatToolTraceEntry {
  name: string;
  summary: string;
  ok: boolean;
  basis: string;
  origin?: string | null;
}

export interface ChatTurnResultResponse {
  conversation_id: string;
  message_id?: string | null;
  user_message_id?: string | null;
  answer: string;
  stop_reason: "answered" | "loop_cap" | "token_budget" | "turn_budget" | "refused" | "cancelled" | "error";
  provider: string;
  model: string;
  citations?: ChatCitation[];
  cards?: Record<string, unknown>[];
  views?: Record<string, unknown>[];
  figures?: ChatFigure[];
  diagnostics?: Record<string, number>;
  tool_trace?: ChatToolTraceEntry[];
  follow_ups?: ChatSuggestion[];
  usage?: ChatTurnUsage;
  error?: string | null;
  replayed?: boolean;
}

/** Null token counts mean the provider reported none. */
export interface ChatTurnUsage {
  input_tokens?: number | null;
  output_tokens?: number | null;
  model_calls?: number;
}

/** How many of the changes a list's filters match keep an evidence checklist. */
export interface ChecklistsBuilt {
  built: number;
  changes: number;
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

export interface CloudEvent {
  specversion: string;
  id: string;
  source: string;
  type: string;
  time: string;
  datacontenttype: string;
  subject?: string | null;
  data: Record<string, unknown>;
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
  attribution?: string | null;
  health_delta?: HealthDeltaSummaryResponse | null;
  touches_hotspot?: number;
  drivers?: RiskDriverResponse[];
  agent_channel?: string | null;
  files?: CommitFileResponse[];
  health?: CommitHealthResponse | null;
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

/** One file a commit touched, with what it cost and what it carries. */
export interface CommitFileResponse {
  path: string;
  lines_added: number;
  lines_deleted: number;
  prior_fixes?: number | null;
}

/** One thing a commit introduced or worsened. */
export interface CommitHealthFindingResponse {
  change_kind: string;
  dimension: string;
  biomarker_type: string;
  severity: string;
  severity_before?: string | null;
  path: string;
  symbol?: string | null;
  line_start?: number | null;
  line_end?: number | null;
  attribution_basis: string;
  reason: string;
  moved_from?: MovedFromResponse | null;
}

/**
 * What a commit did to code health, as computed at index time.
 *
 * Absent on the commit, rather than empty, when the commit was never
 * scanned — the scan is bounded, so older commits routinely have no row and
 * that is not the same claim as "changed nothing".
 */
export interface CommitHealthResponse {
  status: string;
  stale?: boolean;
  introduced_count: number;
  worsened_count: number;
  resolved_count: number;
  improved_count?: number;
  moved_count?: number;
  files_analyzed: number;
  files_skipped: number;
  findings?: CommitHealthFindingResponse[];
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
  attribution?: string | null;
  health_delta?: HealthDeltaSummaryResponse | null;
  touches_hotspot?: number;
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
  agent_commit_count?: number | null;
  agent_pct_of_covered?: number | null;
  attribution_coverage_pct?: number | null;
  avg_entropy: number;
  risk_histogram?: RiskHistogramBucket[];
  moderate_cut?: number | null;
  high_cut?: number | null;
}

export interface CommunityDetailResponse {
  community_id: number;
  label: string;
  cohesion: number;
  conductance?: number | null;
  member_count: number;
  hidden_member_count?: number;
  members: CommunityMember[];
  truncated: boolean;
  neighboring_communities: NeighboringCommunity[];
  health_score?: number | null;
  scored_member_count?: number;
  hot_count?: number;
  dead_count?: number;
  decision_count?: number;
  primary_owner?: string | null;
  primary_owner_file_count?: number;
}

export interface CommunityMember {
  path: string;
  pagerank: number;
  is_entry_point: boolean;
  is_hotspot?: boolean;
  is_dead?: boolean;
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
  hidden_member_count?: number;
}

export interface CommunitySummaryItem {
  community_id: number;
  label: string;
  cohesion: number;
  conductance?: number | null;
  member_count: number;
  hidden_member_count?: number;
  top_file: string;
}

export interface ConcentrationOwner {
  key: string;
  name: string;
  files_owned: number;
  share: number;
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

/** Two areas whose files change together; ``source == target`` for same-area pairs. */
export interface CouplingAreaPair {
  pairs: number;
  unexplained_pairs: number;
  unexplained_share: number;
  strength: number;
  co_changes: number;
  last_co_change?: string | null;
  linked?: boolean | null;
  hidden?: boolean | null;
  source: string;
  target: string;
  top_pairs?: CouplingEdgeResponse[];
}

/** One area's coupling: every kept pair with a file in it. */
export interface CouplingAreaSummary {
  pairs: number;
  unexplained_pairs: number;
  unexplained_share: number;
  strength: number;
  co_changes: number;
  last_co_change?: string | null;
  linked?: boolean | null;
  hidden?: boolean | null;
  path: string;
}

export interface CouplingAreaTotals {
  pairs: number;
  cross_area_pairs: number;
  unexplained_pairs: number;
  area_pairs: number;
  unexplained_area_pairs: number;
  hidden_area_pairs: number;
  unlabelled_pairs: number;
}

export interface CouplingAreasResponse {
  root: string;
  depth: number;
  totals: CouplingAreaTotals;
  areas?: CouplingAreaSummary[];
  area_pairs?: CouplingAreaPair[];
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
  cross_area?: boolean | null;
}

export interface CouplingGraphResponse {
  nodes?: CouplingNodeResponse[];
  edges?: CouplingEdgeResponse[];
  total_edges?: number;
  coupled_files?: number;
  total_files?: number;
  depth?: number | null;
}

export interface CouplingNodeResponse {
  file_path: string;
  module?: string | null;
  score?: number | null;
  nloc?: number;
}

export interface CoverageAddRequest {
  path: string;
}

/** One ingested report's repo-wide figures, as ``crud.load_coverage_history`` reads them. */
export interface CoverageHistoryPoint {
  ingested_at: string;
  ingested_commit_sha: string | null;
  line_coverage_pct: number;
  branch_coverage_pct: number | null;
}

/** How the report's own file entries mapped to the repository at ingest. */
export interface CoverageReportPaths {
  total: number;
  matched: number;
  unmatched: number;
  ambiguous: number;
  unmatched_sample: string[];
}

/**
 * ``GET /health/coverage``. ``basis`` is absent when the graph was not consulted.
 *
 * ``unavailable`` means the graph walk failed, which is not the same as ``none``
 * (no test files to walk from).
 */
export interface CoverageResponse {
  summary: CoverageSummary;
  files: Record<string, unknown>[];
  modules: Record<string, unknown>[];
  modules_total: number;
  basis?: "measured" | "inferred" | "none" | "unavailable" | null;
  inferred?: Record<string, unknown> | null;
  history?: CoverageHistoryPoint[] | null;
}

/** The repository's stored coverage, aggregated. Zero counts and nulls when none is stored. */
export interface CoverageSummary {
  file_count: number;
  covered_lines: number;
  total_lines: number;
  line_coverage_pct: number | null;
  branch_coverage_pct: number | null;
  source_format: string | null;
  source_formats: string[];
  mapping_partial: boolean | null;
  ingested_at: string | null;
  ingested_commit_sha: string | null;
  report_paths: CoverageReportPaths | null;
  freshness: CoverageSummaryFreshness | null;
}

/** Whether the coverage was measured at the commit the index describes. */
export interface CoverageSummaryFreshness {
  status: "current" | "stale" | "unknown";
  indexed_commit: string | null;
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
  lines: number | null;
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
  verification?: string | null;
  preview?: string | null;
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
  safe_to_delete_count?: number | null;
  total_lines: number;
  by_kind: Record<string, unknown>;
  analyzed_at?: string | null;
  gated?: Record<string, unknown>;
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
  kind?: "architectural" | "agreement" | null;
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
  kind?: string;
  scope?: string | null;
  superseded_by: string | null;
  last_code_change: string | null;
  created_at: string;
  updated_at: string;
  evidence_count?: number | null;
  evidence_preview?: EvidencePreview | null;
  hotspot_files?: string[] | null;
  currency?: string | null;
  accepter?: string | null;
  accepter_kind?: string | null;
  accepter_session?: string | null;
  blockers?: string[] | null;
  decision_text_missing?: boolean;
}

/** The resolved decision capture policy for one repository. */
export interface DecisionSettings {
  enabled?: boolean;
  llm?: boolean;
  preset?: string;
  agent_acceptance?: boolean;
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
  agent_acceptance?: boolean | null;
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
  churn_percentile: number;
  is_hotspot: boolean;
  centrality: number;
}

/**
 * A naming document that carries drift somewhere in it.
 *
 * ``findings`` counts the whole document, not assertions about the file that
 * was asked about. A drifted reference resolves to nothing, so no row here
 * can say a document's description of that file is wrong.
 */
export interface DocDriftDocumentDriftResponse {
  document: string;
  findings: number;
}

/**
 * One assertion a document makes that the repository no longer satisfies.
 *
 * ``file_path`` is the *document* to edit, never the target it names. A
 * reader who reads it as the broken file has been told the opposite of the
 * truth, which is why the field keeps the name every other drift surface
 * gives it.
 */
export interface DocDriftFindingResponse {
  id: string;
  file_path: string;
  line_number: number;
  kind: string;
  target: string;
  confidence: number;
  origin: string;
  reason: string;
  raw: string;
  context: string;
  evidence: string[];
  fingerprint: string;
  suggestion?: string | null;
  suggestion_basis?: string | null;
  first_seen_at?: string | null;
  is_new?: boolean;
}

/**
 * A document that names a file the repository still has.
 *
 * Deliberately weaker than a finding: it does not claim the document
 * describes the file, or that its prose is current.
 */
export interface DocDriftReferenceResponse {
  document: string;
  line: number;
  kind: string;
  section?: string;
}

/** Which documents name one file: the reverse view, over HTTP. */
export interface DocDriftReferencesResponse {
  target_path: string;
  references: DocDriftReferenceResponse[];
  references_emitted: number;
  references_total: number;
  documents: number;
  documents_with_drift: DocDriftDocumentDriftResponse[];
  references_basis: string;
  unavailable?: "not_computed" | "index_predates_doc_drift" | "drift_read_failed" | null;
}

/** Findings for a repository, with the rollup that makes them legible. */
export interface DocDriftResponse {
  findings: DocDriftFindingResponse[];
  findings_emitted: number;
  summary: DocDriftSummaryResponse | null;
  unavailable?: "not_computed" | "index_predates_doc_drift" | "drift_read_failed" | null;
}

/**
 * The rollup above the list, computed server-side over the same rows.
 *
 * ``findings_total`` counts every finding matching the query, including any
 * the display cap left out, so a client cannot recompute a page as if it were
 * the repository.
 */
export interface DocDriftSummaryResponse {
  findings_total: number;
  documents: number;
  confidence: Record<string, number>;
  by_kind: Record<string, number>;
  findings_basis: string;
  new_since_last_update?: number | null;
}

export interface EgoGraphResponse {
  nodes: GraphNodeResponse[];
  links: GraphEdgeResponse[];
  center_node_id: string;
  center_git_meta: GitMetadataResponse | null;
  inbound_count: number;
  outbound_count: number;
}

/** One registered embedder, as the settings picker renders it. */
export interface EmbedderEntry {
  id: string;
  env_vars?: string[];
  semantic?: boolean;
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

export interface EventListResponse {
  events: CloudEvent[];
  snapshot_runs?: number;
  empty_reason?: "baseline_only" | "no_match" | "nothing_moved" | null;
  empty_note?: string | null;
}

/** One checklist row: what was found, whether it needs a look, how it is known. */
export interface EvidenceItem {
  kind: string;
  label: string;
  status: "ok" | "attention" | "unknown";
  basis: "measured" | "inferred" | "declared" | "unresolved";
  value?: unknown;
  details: Record<string, unknown>[];
  source: string;
  line?: EvidenceLine | null;
}

/** An item's value as a message key and its parameters, for a client to word. */
export interface EvidenceLine {
  key: string;
  params: Record<string, unknown>;
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

/** A file's health score before and after; ``None`` on a side it was unscored. */
export interface FileScoreShiftResponse {
  path: string;
  before?: number | null;
  after?: number | null;
  change?: number | null;
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

export interface FixAction {
  summary: string;
  steps: FixStep[];
  steps_total: number;
  mechanical: boolean;
}

export interface FixConfidence {
  level: "high" | "medium" | "low";
  reason: string;
}

export interface FixContext {
  label: string;
  value: string;
}

export interface FixEffortEstimate {
  bucket: "S" | "M" | "L" | "XL";
  basis: string;
}

export interface FixFact {
  label: string;
  value: string;
  basis?: "measured" | "inferred" | "unknown";
}

/** ``FixFirstQueue.as_dict()``: the stored fields plus the ``lead`` it derives. */
export interface FixFirstQueueResponse {
  items: FixItem[];
  lead: FixItem | null;
  totals: FixTotals;
  by_improves: Record<string, number>;
  model_version: number;
  basis: Record<string, string | null>;
  counts: QueueCounts;
}

export interface FixGain {
  kind: "health_points" | "performance";
  value: number | null;
  text: string;
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

export interface FixItem {
  id: string;
  rank: number;
  tier: "now" | "next" | "later";
  kind: "refactor" | "perf_fix" | "finding";
  improves: "defect" | "maintainability" | "performance";
  title: string;
  target: FixTarget;
  why: string;
  facts: FixFact[];
  action: FixAction;
  gain: FixGain;
  effort: FixEffortEstimate;
  risk: FixRisk;
  confidence: FixConfidence;
  verify: FixVerify;
  context: FixContext[];
  source: FixSource;
  next_call: ActionCommand;
  why_ranked?: FixRankFact[];
  value?: number | null;
}

export interface FixRankFact {
  factor: string;
  value: string;
}

export interface FixRisk {
  level: "high" | "medium" | "low";
  dependents: number | null;
  files_touched: number;
  text: string;
}

export interface FixSource {
  opportunity_id?: string | null;
  plan_ids?: string[];
  finding_ids?: string[];
}

export interface FixStep {
  order: number;
  text: string;
  file_path: string;
  line?: number | null;
  mechanical?: boolean;
  signature?: string | null;
  call?: string | null;
  command?: string | null;
  notes?: string[];
}

export interface FixTarget {
  file_path: string;
  symbol?: string | null;
  line_start?: number | null;
  line_end?: number | null;
}

export interface FixTest {
  path: string;
  reason: string;
}

export interface FixTotals {
  candidates?: number;
  eligible?: number;
  shown?: number;
  excluded?: Record<string, number>;
  dormant?: number;
}

export interface FixVerify {
  tests: FixTest[];
  tests_total: number;
  command: string | null;
  basis: "measured" | "inferred" | "unknown";
  prerequisite?: string | null;
}

/** Optional per-call provider/model overrides, as chat accepts. */
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
  primary_owner_line_pct?: number | null;
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
  agent_commit_count?: number | null;
  agent_authored_pct?: number | null;
  agent_tier_counts?: Record<string, unknown> | null;
  attribution_coverage?: number | null;
  agent_share_suppressed?: string | null;
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

/**
 * What one commit did to health, as list rows carry it.
 *
 * ``kind`` reads the counts: ``improved`` when it only made things better,
 * ``worsened`` when it only made them worse, ``mixed`` for both, ``neutral``
 * for neither. ``improved`` (findings that measure better) and ``moved`` are
 * null on a stale row, where the stored 0 means unknown; ``files`` stays empty for stored deltas (per-file scores are
 * live-only). ``stale`` means an older analyzer wrote it: the direction holds,
 * the counts do not compare with current rows, and no aggregate counts it.
 */
export interface HealthDeltaSummaryResponse {
  status: string;
  kind: "improved" | "worsened" | "mixed" | "neutral";
  introduced: number;
  worsened: number;
  resolved: number;
  improved?: number | null;
  moved?: number | null;
  stale?: boolean;
  files?: FileScoreShiftResponse[];
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
  verification?: string | null;
  lower_priority?: string | null;
  preview?: string | null;
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
  verification?: string | null;
  lower_priority?: string | null;
  preview?: string | null;
  symbol_id?: string | null;
}

export interface HealthResponse {
  status: string;
  db: string;
  version: string;
}

/**
 * What a change's commits did to health, summed.
 *
 * ``status`` is ``partial`` when a commit was not scanned or skipped files.
 * Only rows from the current analyzer count; the rest are unscanned.
 * ``files_worsened`` counts paths the stored worse findings name (a floor:
 * findings are capped per commit). ``files_improved`` is null until stored
 * deltas carry per-file scores.
 */
export interface HealthRollupResponse {
  status: string;
  kind: "improved" | "worsened" | "mixed" | "neutral";
  scanned_commits: number;
  unscanned_commits: number;
  introduced: number;
  worsened: number;
  resolved: number;
  improved?: number | null;
  moved?: number | null;
  files_improved?: number | null;
  files_worsened: number;
}

export interface HealthTrendAlert {
  kind: string;
  metric: string;
  current: number;
  baseline?: number | null;
  delta: number;
  message: string;
  driver?: string | null;
  structure_delta?: number | null;
  history_delta?: number | null;
}

/** One snapshot in the repo-level history, newest first. */
export interface HealthTrendKpiRow {
  taken_at?: string | null;
  hotspot_health?: number | null;
  average_health: number;
  worst_performer_path?: string | null;
  worst_performer_score?: number | null;
  structure_average?: number | null;
  history_average?: number | null;
  maintainability_average?: number | null;
  doc_drift_count?: number | null;
}

export interface HealthTrendResponse {
  history?: HealthTrendKpiRow[];
  summary: HealthTrendSummary;
  alerts?: HealthTrendAlert[];
  file_deltas?: HealthFileDelta[];
  file_deltas_total?: number;
  file_deltas_worse?: number;
  file_deltas_improved?: number;
  snapshot_count?: number;
  scope?: string;
}

export interface HealthTrendSummary {
  current_hotspot_health?: number | null;
  current_average_health: number;
  previous_hotspot_health?: number | null;
  previous_average_health?: number | null;
  hotspot_delta?: number | null;
  average_delta?: number | null;
  current_structure_deduction?: number | null;
  current_history_deduction?: number | null;
}

/** One file in the triage queue, ranked by impact over effort. */
export interface HealthWorkItem {
  file_path: string;
  score: number;
  nloc: number;
  module?: string | null;
  is_test?: boolean;
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
  open_finding_count?: number;
  biomarkers?: string[];
  effort_bucket: string;
  impact_per_effort: number;
  preview?: string | null;
}

export interface HealthWorkQueueResponse {
  targets?: HealthWorkItem[];
  total?: number;
  finding_total?: number;
  history_only_excluded?: number;
  offset?: number;
  limit?: number;
}

export interface HookToggle {
  enabled: boolean;
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

/** Counts over every hotspot the search matches, whatever the filter. */
export interface HotspotFacets {
  total: number;
  bus_factor_1: number;
  accelerating: number;
  bug_magnet: number;
  with_fixes?: number;
}

export interface HotspotPage {
  items: HotspotResponse[];
  total: number;
  has_more: boolean;
  next_offset?: number | null;
  facets: HotspotFacets;
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
  accelerating?: boolean;
  rank?: number | null;
}

export interface IdentityResponse {
  signed_in: boolean;
  hints_enabled: boolean;
}

/** One file on the impact / effort plane. */
export interface ImpactEffortPoint {
  file_path: string;
  effort_lines: number;
  effort_basis: string;
  recoverable_health: number;
  tier?: string | null;
  fix_rank?: number | null;
}

/** Every file the work queue's filters keep, up to ``cap``. */
export interface ImpactEffortResponse {
  points?: ImpactEffortPoint[];
  plotted?: number;
  total?: number;
  cap?: number;
  effort_midline_lines?: number;
  gain_midline_points?: number;
  history_only_excluded?: number;
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

export interface KnowledgeMatrixAuthor {
  key: string;
  name: string;
  email: string | null;
  last_commit_at: string | null;
  active: boolean;
}

/** Every code file's top-5 human authors, positional and indexed for size. */
export interface KnowledgeMatrixResponse {
  authors: KnowledgeMatrixAuthor[];
  dirs: string[];
  files: ([number, string, number, number | null, boolean, number, number | null, [number, number][], boolean])[];
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

/** Where a finding's code was before the commit moved it. */
export interface MovedFromResponse {
  path: string;
  symbol?: string | null;
}

export interface NeighboringCommunity {
  community_id: number;
  label: string;
  cross_edge_count: number;
}

export interface NextAction {
  id: string;
  rule: string;
  tier: "act_now" | "plan" | "improve_signal";
  horizons: ("week" | "quarter")[];
  severity: "critical" | "high" | "medium" | "low";
  /** Verb first; paths and symbols wrapped in backticks. */
  title: string;
  impact: string;
  why: ActionWhy[];
  target: ActionTarget;
  surface: string;
  effort: "S" | "M" | "L";
  confidence: "high" | "medium";
  /** 0-4: the Fix first value band, or what the severity says. */
  value?: number;
  /** Value x confidence / effort: the order inside a tier. */
  priority?: number;
  done_when: string;
  command?: string | null;
  marker?: string | null;
  evidence_ids: string[];
  evidence_total: number;
  includes: string[];
  fingerprint: string;
  details?: ActionDetail[];
  details_total?: number;
  commands?: ActionCommand[];
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

export interface OnboardingPatch {
  welcome_seen_repo?: string | null;
  setup_pill_hidden?: boolean | null;
  hosted_card_hidden?: boolean | null;
}

/** Per-machine onboarding state, plus the switches that hide hosted surfaces. */
export interface OnboardingState {
  welcome_seen?: Record<string, string>;
  setup_pill_hidden?: boolean;
  hosted_card_hidden?: boolean;
  hints_enabled: boolean;
  signed_in: boolean;
}

/** Every display name and canonical email folded into one person. */
export interface OwnerAliases {
  names: string[];
  emails: string[];
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
  kind: "human" | "bot" | "agent";
  files_owned: number;
  hotspots_owned: number;
  silo_modules: number;
  dead_code_files_owned: number;
  dead_code_lines_owned: number;
  commit_count_90d: number | null;
  commit_count_90d_at_least?: number | null;
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
  kind: "human" | "bot" | "agent";
  aliases: OwnerAliases;
  files_owned: number;
  hotspots_owned: number;
  silo_modules: number;
  dead_code_files_owned: number;
  dead_code_lines_owned: number;
  commit_count_90d: number | null;
  commit_count_90d_at_least?: number | null;
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
}

export interface OwnerRef {
  key: string;
  name: string;
}

/** Repository knowledge risk, people only (automation owns nothing). */
export interface OwnersSummaryResponse {
  truck_factor: number | null;
  people: number;
  automation_identities: number;
  owners: number;
  tracked_files: number;
  owned_files: number;
  single_owner_files: number;
  single_owner_hotspots: number;
  concentration: OwnershipConcentration;
  silo_modules: SiloModule[];
  activity_window_days: number;
  active_contributors: number;
  activity_complete: boolean;
  quiet_owners: QuietOwner[];
  quiet_owners_total: number;
}

export interface OwnershipConcentration {
  top_n: number;
  share: number | null;
  owners: ConcentrationOwner[];
}

export interface OwnershipEntry {
  module_path: string;
  primary_owner: string | null;
  owner_pct: number | null;
  file_count: number;
  is_silo: boolean;
}

export interface PackageUsageComponent {
  name: string;
  files: number;
  owners: string[];
}

export interface PackageUsageDeclaration {
  package_key: string;
  version?: string | null;
  declared_in: string;
  dev?: boolean;
}

/** One module the file imports and how it was matched to the package. */
export interface PackageUsageModule {
  name: string;
  mapping: "declared" | "inferred";
}

export interface PackageUsageReachability {
  state: string;
  basis: string;
}

/** Where one package is imported: sites, components, owner teams, reachability. */
export interface PackageUsageResponse {
  package: string;
  ecosystem?: string | null;
  declared: PackageUsageDeclaration[];
  summary: PackageUsageSummary;
  sites: PackageUsageSite[];
  components: PackageUsageComponent[];
  method: Record<string, string>;
  note?: string | null;
}

/** One file importing the package. */
export interface PackageUsageSite {
  path: string;
  modules: PackageUsageModule[];
  symbols: string[];
  mapping: "declared" | "inferred";
  line?: number | null;
  statement?: string | null;
  component?: string | null;
  owners: string[];
  owner_counts?: Record<string, number> | null;
  reachability: PackageUsageReachability;
}

export interface PackageUsageSummary {
  files: number;
  components: number;
  reachability: Record<string, number>;
  inferred_files: number;
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
  digest?: string;
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

/** Branches on changed lines, reported beside patch coverage, never blended into it. */
export interface PatchCoverageBranches {
  branch_taken: number;
  branch_total: number;
  branch_coverage_pct: number | null;
  partial_line_count: number;
  threshold: number | null;
  gate: "pass" | "fail" | "no_data" | "not_set" | "too_small";
}

export interface PatchCoverageFile {
  file_path: string;
  status: "measured" | "not_in_report" | "no_line_data" | "no_coverable_changes" | "stale_file";
  changed_line_count: number;
  coverable_line_count: number;
  covered_line_count: number;
  patch_coverage_pct: number | null;
  uncovered_ranges: number[][];
  branch_taken: number;
  branch_total: number;
  partial_ranges: number[][];
  risk: PatchCoverageFileRisk | null;
  hints: PatchCoverageTestHint[] | null;
}

export interface PatchCoverageFileCounts {
  measured: number;
  not_in_report: number;
  no_line_data: number;
  no_coverable_changes: number;
  stale_file: number;
  out_of_scope: number;
}

export interface PatchCoverageFileRisk {
  fix_pressure: number | null;
  dependents: number | null;
  hotspot: boolean | null;
  bug_magnet: boolean | null;
  basis: "git" | "index" | "git_and_index" | "unavailable";
  risky: boolean;
  reasons: string[];
}

/** A file whose coverage changed on lines the change did not touch. */
export interface PatchCoverageOutsideChange {
  file_path: string;
  status: "changed" | "no_longer_measured";
  newly_uncovered_ranges: number[][];
  newly_uncovered_line_count: number;
  newly_covered_line_count: number;
  base_pct: number | null;
  head_pct: number | null;
  causes: PatchCoverageOutsideChangeCause[] | null;
}

/** A changed file that explains coverage lost outside the change. */
export interface PatchCoverageOutsideChangeCause {
  kind: "test_deleted" | "test_modified" | "dependent_changed";
  path: string;
  basis: "per_test" | "graph" | "name";
}

/** One path-scoped gate from ``coverage.gates``, judged on the change. */
export interface PatchCoveragePathGate {
  name: string;
  paths: string[];
  threshold: number | null;
  informational: boolean;
  measured_file_count: number;
  unmeasured_file_count: number;
  covered_line_count: number;
  coverable_line_count: number;
  patch_coverage_pct: number | null;
  gate: "pass" | "fail" | "no_data" | "not_set" | "too_small";
}

/** Project coverage at the change's base against its head. */
export interface PatchCoverageProject {
  basis: "base_report" | "history";
  base_commit: string | null;
  head_commit: string | null;
  base: PatchCoverageProjectTotals | null;
  head: PatchCoverageProjectTotals | null;
  delta_pct: number | null;
  max_drop: number | null;
  gate: "pass" | "fail" | "no_data" | "not_set" | "too_small";
  incomparable: string[];
  outside_change: PatchCoverageOutsideChange[] | null;
  outside_change_note: string | null;
}

export interface PatchCoverageProjectTotals {
  covered_line_count: number;
  coverable_line_count: number;
  coverage_pct: number | null;
}

export interface PatchCoverageResponse {
  patch_coverage_pct: number | null;
  covered_line_count: number;
  coverable_line_count: number;
  threshold: number | null;
  min_coverable_lines: number | null;
  gate: "pass" | "fail" | "no_data" | "not_set" | "too_small";
  file_counts: PatchCoverageFileCounts;
  files: PatchCoverageFile[];
  scope: PatchCoverageScope;
  path_gates: PatchCoveragePathGate[];
  risky: PatchCoverageRisky | null;
  project: PatchCoverageProject | null;
  branches: PatchCoverageBranches | null;
}

/** Patch coverage over the measured files history marks as risky. */
export interface PatchCoverageRisky {
  file_count: number;
  covered_line_count: number;
  coverable_line_count: number;
  patch_coverage_pct: number | null;
  threshold: number | null;
  gate: "pass" | "fail" | "no_data" | "not_set" | "too_small";
}

export interface PatchCoverageScope {
  label: string;
  source_formats: string[];
  reports: string[];
  report_path_count: number | null;
  unmatched_report_path_count: number | null;
  mapping_partial: boolean;
  measured_commit: string | null;
  freshness: "current" | "stale" | "unknown";
  ignored_file_count: number;
  config_errors: string[];
  branch_data: "per_line" | "none" | "stored_before" | null;
}

/** Where to extend the tests for one uncovered range. */
export interface PatchCoverageTestHint {
  range: number[];
  symbol: string | null;
  tests: string[];
  basis: "per_test" | "call_graph" | "import_graph" | "none";
  total: number;
}

/**
 * A period headline: items that made health better first, then worse.
 *
 * Items are changes on ``/changes/summary`` and commits on
 * ``/commits/health-summary``. ``unscanned`` items have no delta from the
 * current analyzer ("not yet analysed") and are in none of the four kinds;
 * recent commits refill as updates rescan them.
 */
export interface PeriodHealthSummaryResponse {
  since?: string | null;
  until?: string | null;
  total: number;
  improved: number;
  mixed: number;
  worsened: number;
  neutral: number;
  unscanned: number;
  findings_resolved: number;
  findings_improved?: number | null;
  findings_introduced: number;
  findings_worsened: number;
  net_findings: number;
  categories?: Record<string, number> | null;
}

/** Result of pinning a file's doc (issue #812). */
export interface PinDocResponse {
  file_path: string;
  pinned: boolean;
}

/** A function's stored size measures; an absent one was not measured. */
export interface PlanMeasuresResponse {
  ccn?: number | null;
  nloc?: number | null;
  params?: number | null;
}

/**
 * What happened to a plan the index resolved as no longer detected.
 *
 * ``applied`` means the measures are consistent with the plan being applied:
 * the target shed at least half the predicted decision points and a function
 * fitting the extracted slice appeared beside it.
 */
export interface PlanPayoffResponse {
  outcome: string;
  resolved_commit?: string | null;
  resolved_at?: string | null;
  before?: PlanMeasuresResponse | null;
  after?: PlanMeasuresResponse | null;
  realised?: Record<string, number> | null;
  new_symbol?: string | null;
}

export interface PlanRiskResponse {
  kind: string;
  text: string;
  ref?: string | null;
}

/**
 * What the map is counting.
 *
 * Every count in the payload is over ``visible``. The category totals are
 * reported whether or not included, so a client can offer "show tests (N)".
 */
export interface PopulationBreakdown {
  total: number;
  visible: number;
  tests?: number;
  examples?: number;
  docs?: number;
  include_tests?: boolean;
  include_examples?: boolean;
  include_docs?: boolean;
}

/**
 * What the PR bot stored for a pull request: code health before and after, and new findings.
 *
 * A host that runs the bot fills it; a local server never does.
 */
export interface PrReviewSummary {
  health_before?: number | null;
  health_after?: number | null;
  introduced?: number | null;
  url?: string | null;
}

/** One provider in the catalog, as the settings picker renders it. */
export interface ProviderEntry {
  id: string;
  name: string;
  models?: string[];
  default_model?: string | null;
  configured?: boolean;
  requires_key?: boolean;
  accepts_base_url?: boolean;
  local_server?: boolean;
  local_model?: boolean;
  env_vars?: string[];
  setup_hint?: string;
  detected?: "signed_in" | "installed" | "env" | "saved" | null;
}

export interface ProviderModelEntry {
  id: string;
  label?: string | null;
}

/**
 * Models a provider offers, newest-looking first after the default.
 *
 * ``source`` is ``live`` when the provider listed them and ``catalog`` when
 * the built-in list stands in, with ``error`` saying why.
 */
export interface ProviderModelsResponse {
  models: ProviderModelEntry[];
  source: "live" | "catalog";
  default_model: string;
  error?: string | null;
}

export interface ProviderStatusResponse {
  active: ActiveProviderSelection;
  providers?: ProviderEntry[];
  flag_only_providers?: string[];
  embedders?: EmbedderEntry[];
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

export interface PublishRequest {
  repo_id: string;
}

/** ``repowise publish --format json``, passed through unchanged. */
export interface PublishResponse {
  outcome: string;
  message: string;
  url?: string | null;
  details?: string[];
  open_url?: string | null;
  repo?: string | null;
}

export interface QueueCounts {
  inventory?: number;
  in_scope?: number;
  eligible?: number;
  due?: number;
  shown?: number;
  excluded?: Record<string, number>;
  gated?: number;
}

export interface QuietOwner {
  key: string;
  name: string;
  last_commit_at: string | null;
  files_owned: number;
  changing_files_owned: number;
}

/** What the ``fix_first`` scope leaves out of a page's filtered set. */
export interface RefactoringHiddenCounts {
  total?: number;
  by_reason?: Record<string, number>;
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
  scope?: "fix_first" | "all";
  hidden?: RefactoringHiddenCounts | null;
  counts?: QueueCounts | null;
  gated?: Record<string, Record<string, unknown>> | null;
}

/**
 * One opportunity: its ordered steps, evidence, validation and plans.
 *
 * ``extra="allow"``: the base row is composed in core and this response
 * spreads an evidence block over it, so the declared keys are the stable
 * part and anything else passes through rather than being dropped.
 */
export interface RefactoringOpportunityDetailResponse {
  found: boolean;
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
  preview?: string | null;
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

/**
 * One plan read alone: what other layers say about its target. Lists stay
 * on :class:`RefactoringPlanResponse` and never carry these; both are absent
 * on a plan that was never checked.
 */
export interface RefactoringPlanDetailResponse {
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
  preview?: string | null;
  governed_by?: string[] | null;
  risks?: PlanRiskResponse[] | null;
  recipe?: Record<string, unknown> | null;
  payoff?: PlanPayoffResponse | null;
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
  preview?: string | null;
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

/**
 * The code-generation switch plus the model it will use.
 *
 * ``provider`` / ``model`` are read-only: they come from the same resolver
 * chat uses, so the user configures a model once. Never carries a key.
 */
export interface RefactoringSettings {
  enabled?: boolean;
  provider?: string | null;
  model?: string | null;
}

/** The one writable field, ``refactoring.llm.enabled``. */
export interface RefactoringSettingsUpdate {
  enabled: boolean;
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
  design_total?: number | null;
  performance_total?: number | null;
  small_effort_total?: number | null;
  health_recovery_total?: number | null;
  negligible_health_total?: number | null;
  best_health_gain?: number | null;
}

export interface RefactoringTargetsResponse {
  summary: RefactoringSummary;
  plans: RefactoringPlanResponse[];
  gated?: Record<string, Record<string, unknown>>;
}

export interface RefactoringTypeCount {
  type: string;
  count: number;
}

export interface RelatedWorkFile {
  file_path: string;
  lenses?: Record<string, RelatedWorkLens>;
}

/** One row another lens holds for the file, compact enough to list. */
export interface RelatedWorkItem {
  lens: "findings" | "fix_first" | "refactoring" | "performance" | "dead_code";
  id: string;
  kind?: string | null;
  title?: string | null;
  symbol?: string | null;
  severity?: string | null;
  tier?: string | null;
  rank?: number | null;
  line?: number | null;
  code_origin?: string | null;
  deprecated?: boolean | null;
}

export interface RelatedWorkLens {
  items?: RelatedWorkItem[];
  total?: number;
  preview?: string | null;
}

/** The files to look up, repo-relative. Validated by the route. */
export interface RelatedWorkRequest {
  file_paths: string[];
}

/** ``RelatedWork.as_dict()``: files in request order. */
export interface RelatedWorkResponse {
  files?: RelatedWorkFile[];
  per_lens_limit?: number;
}

/** A release's "By the numbers", from ``scripts/release_stats.py``. */
export interface ReleaseStatsModel {
  since: string;
  days: number;
  prs: number;
  contributors: number;
  first_time_contributors?: number | null;
  files_changed: number;
  lines_added: number;
  lines_removed: number;
  types: ReleaseTypeCountsModel;
  test_files_touched: number;
  test_files_new: number;
  issues_closed: number;
}

export interface ReleaseTypeCountsModel {
  fixes?: number;
  features?: number;
  perf?: number;
  refactors?: number;
  docs?: number;
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
  index_scope?: Record<string, unknown> | null;
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

export interface RetiredPageDetail {
  archive_id: string;
  page_id: string;
  repository_id: string;
  retired_at: string;
  reason: string;
  superseded_by: string | null;
  title: string;
  page: Record<string, unknown>;
  versions: Record<string, unknown>[];
}

export interface RetiredPageSummary {
  archive_id: string;
  page_id: string;
  repository_id: string;
  retired_at: string;
  reason: string;
  superseded_by: string | null;
  title: string;
}

export interface ReviewerEntry {
  email: string;
  files: number;
  ownership_pct: number;
  name?: string | null;
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

/** One agent's savings, labelled from the identity registry. */
export interface SavingsAgentRow {
  agent: string;
  agent_display_name?: string | null;
  events?: number;
  saved_input_tokens?: number;
}

/**
 * One bucket of a savings breakdown.
 *
 * ``group`` is nullable because one bucket genuinely has no name: an event
 * written before its rate could be resolved has no model, and that is a real
 * quantity -- how much saving carries no pricing evidence -- rather than a
 * gap to drop from the list.
 */
export interface SavingsBreakdownRow {
  group?: string | null;
  events?: number;
  saved_input_tokens?: number;
}

/** One kind of observed opportunity. Never part of achieved savings. */
export interface SavingsOpportunityRow {
  kind: string;
  observations?: number;
  estimated_potential_input_tokens?: number;
}

/**
 * What agents avoided in one repository over one window.
 *
 * Every figure comes from the canonical savings ledger through the one core
 * report service, so this response, the repository overview headline and
 * ``repowise saved`` cannot disagree -- which the three of them did, in four
 * separate ways, when each aggregated and priced the ledger for itself.
 *
 * Three things it deliberately keeps apart. *Measured* reductions are known
 * before/after sizes from an operation that really ran; *inferred* avoidance
 * is a documented counterfactual. *Priced* and *unpriced* tokens are split
 * because an event records the rate it was worth at the time and some events
 * carry none, and reporting a total as though it were all priced would be a
 * guess. *Observed opportunities* are things that could have been saved and
 * were not: they never enter the headline.
 *
 * ``available`` is False when the repository has no savings sidecar at all,
 * which means "nothing has been measured here" and is different from a
 * measured zero.
 */
export interface SavingsResponse {
  available: boolean;
  window_days?: number | null;
  as_of?: string;
  first_event_at?: string | null;
  last_event_at?: string | null;
  unique_events?: number;
  successful_or_usable_partial_events?: number;
  saving_interactions?: number;
  mcp_queries_answered?: number;
  dead_ends?: number;
  saved_input_tokens?: number;
  measured_saved_input_tokens?: number;
  inferred_saved_input_tokens?: number;
  priced_saved_input_tokens?: number;
  unpriced_saved_input_tokens?: number;
  priced_input_savings_usd?: number;
  saved_output_tokens?: number | null;
  priced_saved_output_tokens?: number;
  unpriced_saved_output_tokens?: number;
  priced_output_savings_usd?: number;
  baseline_events?: number;
  reducing_events?: number;
  baseline_input_tokens?: number;
  baseline_saved_input_tokens?: number;
  input_reduction_ratio?: number | null;
  input_reduction_ratio_p90?: number | null;
  per_operation?: SavingsBreakdownRow[];
  per_surface?: SavingsBreakdownRow[];
  per_agent?: SavingsAgentRow[];
  per_model?: SavingsBreakdownRow[];
  per_day?: SavingsBreakdownRow[];
  opportunity_count?: number;
  opportunity_tokens_excluded?: number;
  per_opportunity_kind?: SavingsOpportunityRow[];
  missed_events?: number;
  missed_tokens_est?: number;
  missed_window_days?: number;
  reread_events?: number;
  reread_tokens_est?: number;
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
  path_class?: "source" | "test" | "docs";
}

export interface SecuritySummaryResponse {
  scanned_at?: string | null;
  total?: number;
  class_counts?: Record<string, number>;
  source_high?: number;
}

export interface SelectionProvenance {
  provider?: string | null;
  model?: string | null;
  base_url?: string | null;
}

export interface SetActiveProviderRequest {
  provider: string;
  model?: string | null;
  base_url?: string | null;
  repo_id?: string | null;
}

export interface SetApiKeyRequest {
  api_key: string;
  repo_id?: string | null;
}

export interface SetupAgent {
  id: string;
  display_name: string;
  tier?: string | null;
  docs_url?: string | null;
  installed: boolean;
  connected: boolean;
  registrations?: SetupRegistration[];
  config_files?: Record<string, string[]>;
  first_call_at?: string | null;
  last_call_at?: string | null;
}

/** Every call to this repo's index, whichever agent made it. */
export interface SetupCalls {
  first_call_at?: string | null;
  last_call_at?: string | null;
  unattributed?: boolean;
}

export interface SetupCheck {
  id: string;
  name: string;
  level: "ok" | "warn" | "fail";
  detail?: string;
  fix?: string | null;
}

export interface SetupCoverage {
  ingested_at?: string | null;
  configured_paths?: string[];
  discovered?: SetupCoverageReport[];
}

export interface SetupCoverageReport {
  path: string;
  modified_at: string;
  age_days: number;
}

export interface SetupFileChange {
  path: string;
  action: string;
  scope: string;
  reason?: string | null;
}

export interface SetupIndex {
  state: "up_to_date" | "behind" | "incomplete";
  indexed_commit?: string | null;
  live_head?: string | null;
  indexed_at?: string | null;
  file_count?: number;
  parse_error_count?: number;
  parse_error_files?: string[];
}

export interface SetupModel {
  docs_mode: "none" | "deterministic" | "llm";
  active_provider?: string | null;
  active_model?: string | null;
  configured_providers?: string[];
}

export interface SetupRegistration {
  method: string;
  scope: string;
  config_path: string;
}

export interface SetupResponse {
  repo_id: string;
  generated_at: string;
  index: SetupIndex;
  agents_available: boolean;
  agents?: SetupAgent[];
  calls_available: boolean;
  calls: SetupCalls;
  sync: SetupSync;
  rewrite_hook: SetupRewriteHook;
  coverage_available: boolean;
  coverage: SetupCoverage;
  model_available: boolean;
  model: SetupModel;
  checks_available: boolean;
  checks?: SetupCheck[];
  onboarding: OnboardingState;
  writes_allowed: boolean;
}

export interface SetupRewriteHook {
  state: "installed" | "not_installed" | "not_applicable" | "unknown";
  detail?: string;
}

export interface SetupSync {
  installed?: boolean | null;
  detail?: string;
}

/** What a write did. ``ok`` is the command's own success. */
export interface SetupWriteResponse {
  ok: boolean;
  command: string;
  message?: string;
  output?: string[];
  files?: SetupFileChange[];
}

export interface SiloModule {
  module: string;
  owner: OwnerRef;
  owned_files: number;
  file_count: number;
  share: number;
}

/** One change in reading order; ``signals`` holds each signal that fired. */
export interface Suspect {
  id: string;
  ref?: string | null;
  title: string;
  merged_at?: string | null;
  category: string;
  head_sha: string;
  files_changed: number;
  provenance?: string | null;
  rank: number;
  signals: Record<string, SuspectSignal>;
}

export interface SuspectSignal {
  rank: number;
  evidence: string[];
}

/** A symptom and the window to rank the changes merged in. */
export interface SuspectsRequest {
  symptom?: string;
  files?: string[];
  trace?: string;
  since?: string | null;
  until?: string | null;
  limit?: number;
}

export interface SuspectsResponse {
  window: Record<string, string>;
  candidates: number;
  candidates_capped: boolean;
  texts_missing?: number;
  symptom_files?: string[];
  suspects: Suspect[];
  note: string;
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

export interface TestImpactAggregate {
  file_count: number;
  ingested_commit?: string | null;
  freshness: "current" | "stale" | "unknown";
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
  aggregate?: TestImpactAggregate | null;
}

export interface TestImpactEvidence {
  basis: "measured" | "inferred";
  source_file: string;
  via: "coverage-map" | "call-graph" | "import-graph";
  match?: "file" | null;
  source_format?: string | null;
}

export interface TestImpactFile {
  source_file: string;
  status: "measured" | "inferred" | "unknown" | "deleted";
  change_status?: string | null;
  head_present?: boolean;
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
  deleted_files?: string[];
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
  stale?: boolean;
  stale_reason?: "test_file_missing" | "test_file_changed" | null;
}

export interface TransitiveEntry {
  path: string;
  depth: number;
  via?: string | null;
}

/**
 * Visible files below ``min_members``; almost all have no dependency edge.
 *
 * ``files`` is the head by PageRank, capped.
 */
export interface UnclusteredFiles {
  file_count: number;
  files?: string[];
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
  highlights?: ChangelogHighlightModel[];
  latest_highlights?: string[];
}

export interface WebhookResponse {
  event_id: string;
  status?: string;
}

export interface WinsFileGain {
  path: string;
  before: number;
  after: number;
  gain: number;
}

/**
 * Files commits in the window touched whose score rose between the snapshot
 * at ``baseline_at`` and the one at ``current_at``.
 */
export interface WinsFilesImproved {
  count: number;
  baseline_at?: string | null;
  current_at?: string | null;
  top?: WinsFileGain[];
}

/**
 * Findings commits in the window resolved. Resolved findings are stored as
 * per-commit counts, so the top items are the commits that resolved most.
 */
export interface WinsFindingsResolved {
  count: number;
  commits: number;
  top_commits?: WinsResolvingCommit[];
}

/** A refactoring plan that landed: its target is gone or shrank as planned. */
export interface WinsRefactoring {
  file_path: string;
  target_symbol: string;
  refactoring_type: string;
  resolved_at?: string | null;
  before_ccn?: number | null;
  after_ccn?: number | null;
  before_nloc?: number | null;
  after_nloc?: number | null;
}

export interface WinsRefactorings {
  count: number;
  top?: WinsRefactoring[];
}

export interface WinsResolvingCommit {
  sha: string;
  subject: string;
  author_name: string;
  committed_at?: string | null;
  resolved: number;
}

/**
 * What got better since ``since``. Positive only.
 *
 * With ``author`` every section narrows to that person's commits and the
 * files they touched. ``unavailable`` names wins the index cannot answer
 * from stored data (no history is kept for them).
 */
export interface WinsResponse {
  since: string;
  until?: string | null;
  author?: string | null;
  findings_resolved: WinsFindingsResolved;
  files_improved: WinsFilesImproved;
  refactorings_landed: WinsRefactorings;
  unavailable?: string[];
}

export interface WorkspaceActionsResponse {
  repos: WorkspaceRepoActions[];
  cross_repo: WorkspaceCrossRepoAction[];
}

/**
 * Architecture-complexity metrics over the system graph (Phase 6).
 *
 * Every measured figure is ``None`` when there is no graph, or when its
 * ``evidence`` is insufficient: nothing seen is not a 10/10.
 */
export interface WorkspaceArchitectureResponse {
  node_count?: number;
  structural_edge_count?: number;
  propagation_cost?: number | null;
  propagation_cost_pct?: number | null;
  core_size?: number | null;
  core_ratio?: number | null;
  core_members?: string[];
  cycle_count?: number | null;
  cycle_count_capped?: boolean;
  conformance_violations?: number;
  architecture_type?: string;
  score?: number | null;
  role_breakdown?: Record<string, number>;
  roles?: WorkspaceNodeArchitectureRole[];
  generated_at?: string;
  evidence?: WorkspaceGraphEvidence | null;
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
  undetermined_targets?: string[];
  evidence?: WorkspaceGraphEvidence | null;
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
  side?: string | null;
  comparison_source?: string | null;
  comparison_key?: string | null;
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
  evidence?: WorkspaceCoChangeEvidence | null;
}

/** Bounded supporting evidence for one co-change pair (#483). */
export interface WorkspaceCoChangeEvidence {
  authors?: string[];
  commit_pairs?: Record<string, unknown>[];
  max_gap_hours?: number;
}

/** What declared structure connects one co-changing file pair. */
export interface WorkspaceCoChangeStructure {
  pair_links: WorkspaceContractLinkEntry[];
  repo_links_total: number;
  repo_links_by_type: Record<string, number>;
  source_file_links: number;
  target_file_links: number;
}

export interface WorkspaceCoChangesResponse {
  co_changes: WorkspaceCoChangeEntry[];
  total: number;
  total_mined?: number;
  per_repo_pair_cap?: number | null;
  total_cap?: number | null;
  truncated_by?: "total" | "per_repo_pair" | null;
}

export interface WorkspaceCodeApiCoverage {
  manifests?: number;
  published?: number;
  unsupported_ecosystem?: number;
  providers?: number;
  consumers?: number;
  linked_providers?: number;
  published_ratio?: number | null;
  linked_ratio?: number | null;
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
  total_capped?: boolean;
  violating_repos?: string[];
  cycles_determined?: boolean | null;
  evidence?: WorkspaceGraphEvidence | null;
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
  origin?: string | null;
  meta?: Record<string, unknown>;
}

export interface WorkspaceContractLinkEntry {
  contract_id: string;
  contract_type: string;
  match_type: string;
  strategy?: string | null;
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
  consumer_contract_id?: string | null;
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

export interface WorkspaceCrossRepoAction {
  kind: "breaking_contract";
  title: string;
  impact: string;
  count: number;
  repos: string[];
}

export interface WorkspaceCrossRepoSummary {
  co_change_count?: number;
  package_dep_count?: number;
  package_diagnostic_count?: number;
  package_diagnostics_emitted?: number;
  package_diagnostic_codes?: string[];
  top_connections?: Record<string, unknown>[];
}

export interface WorkspaceDependencyCycle {
  nodes?: string[];
  edge_ids?: string[];
  length?: number;
  leg_kinds?: string[][];
  async_legs?: number;
}

/**
 * One evidence item: OpenSSF Scorecard check keys plus ``method`` and ``basis``.
 *
 * ``score`` is 0-10, -1 when inconclusive, ``None`` on a descriptive item.
 * ``data`` holds the item's full figures; its keys depend on ``name``.
 */
export interface WorkspaceEstateCheck {
  name: string;
  score?: number | null;
  reason: string;
  details?: string[];
  method: string;
  basis: "measured" | "inferred" | "declared" | "unresolved" | "gated";
  data?: Record<string, unknown> | null;
  documentation?: WorkspaceEstateCheckDocs | null;
}

export interface WorkspaceEstateCheckDocs {
  url: string;
  short: string;
}

export interface WorkspaceEstateGenerator {
  name: string;
  version: string;
  stamp: string;
}

export interface WorkspaceEstateRepo {
  alias?: string | null;
  name: string;
  head_commit?: string | null;
  last_snapshot?: WorkspaceEstateSnapshot | null;
  indexed?: boolean;
  index_state?: "behind" | "unreadable" | null;
}

/** ``GET /api/workspace/estate``: the estate report document. */
export interface WorkspaceEstateResponse {
  schema_version: number;
  kind: "estate";
  generated_at: string;
  generator: WorkspaceEstateGenerator;
  scope: WorkspaceEstateScope;
  notice: string;
  repos?: WorkspaceEstateRepo[];
  sections?: WorkspaceEstateSection[];
}

export interface WorkspaceEstateScope {
  kind: "workspace";
  name: string;
}

export interface WorkspaceEstateSection {
  id: string;
  title: string;
  summary: string;
  repo?: string | null;
  checks?: WorkspaceEstateCheck[];
  failed?: boolean;
}

export interface WorkspaceEstateSnapshot {
  taken_at: string;
  stamp?: string | null;
}

/** One reason a graph's evidence is short of measured. */
export interface WorkspaceEvidenceReason {
  code: string;
  state: string;
  message: string;
  subjects?: string[];
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
  in_service_consumers?: WorkspaceUnmatchedConsumer[];
  providers_by_layer?: Record<string, number>;
  consumers_by_layer?: Record<string, number>;
  http_consumers_unresolved?: number;
  http_consumer_coverage?: number | null;
  http_consumers_linked?: number | null;
  http_link_coverage?: number | null;
  unrecognised_call_files?: Record<string, number> | null;
  unrecognised_call_file_count?: number | null;
  topic_names_unresolved?: number;
  unrecognised_message_files?: Record<string, number> | null;
  unrecognised_message_file_count?: number | null;
  symbol_identity?: Record<string, WorkspaceSymbolIdentity>;
  schema_coverage?: WorkspaceSchemaCoverage;
  code_api?: WorkspaceCodeApiCoverage;
  openapi?: WorkspaceOpenApiCoverage;
}

export interface WorkspaceGraphEdge {
  source: string;
  target: string;
  type: string;
  strength?: number;
  label?: string | null;
}

/**
 * How much evidence the system graph rests on.
 *
 * Mirrors ``repowise.core.workspace.graph_evidence.GraphEvidence``. When
 * ``determined`` is false, a score, cycle verdict or impact set over the
 * graph would be vacuous and is reported as not determined.
 */
export interface WorkspaceGraphEvidence {
  state: "measured" | "partial" | "insufficient";
  determined: boolean;
  summary?: string;
  reasons?: WorkspaceEvidenceReason[];
  services?: number;
  runtime_edges?: number;
  build_edges?: number;
  providers?: number;
  consumers?: number;
}

export interface WorkspaceGraphNode {
  repo_id: string;
  name: string;
  file_count?: number;
  coverage_pct?: number;
  health_score?: number | null;
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

/**
 * Links between two services, read from ``system_graph.json``, ``contracts.json``
 * and ``cross_repo_edges.json``. The three are written together by one
 * workspace update; ``stale_graph`` is true when the graph and the contracts
 * record different repo heads, and rows may then be missing.
 */
export interface WorkspaceLinksBetweenResponse {
  a: string;
  b: string;
  stale_graph?: boolean;
  links: WorkspaceServiceLinkEntry[];
  total: number;
  totals: WorkspaceServiceLinksTotal[];
}

export interface WorkspaceNodeArchitectureRole {
  id: string;
  repo?: string;
  name?: string;
  visibility_fan_in?: number;
  visibility_fan_out?: number;
  role?: string;
}

export interface WorkspaceOpenApiCoverage {
  documents?: number;
  parsed_documents?: number;
  unresolved_documents?: number;
  operations?: number;
  providers?: number;
  schemas_merged?: number;
  spec_only_providers?: number;
  request_states?: Record<string, number>;
  response_states?: Record<string, number>;
  refusal_reasons?: Record<string, number>;
}

export interface WorkspaceOrphanProvider {
  repo: string;
  file_path: string;
  contract_id: string;
  contract_type: string;
  other_files?: string[];
}

/** One manifest dependency of one repo on another, as ``cross_repo_edges.json`` stores it. */
export interface WorkspacePackageDepEntry {
  source_repo: string;
  source_manifest?: string;
  target_repo: string;
  target_package?: string;
  kind?: string;
  target_manifest?: string;
  requested_version?: string | null;
  scope?: string;
  resolution_basis?: string;
}

export interface WorkspaceRepoActions {
  alias: string;
  repo_id: string | null;
  status: "available" | "unavailable";
  reason?: string;
  horizons?: Record<string, ActionHorizon>;
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
  topic_names_unresolved?: number;
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
  fresh_page_count?: number;
  doc_coverage_pct?: number;
  hotspot_count?: number;
  health_score?: number | null;
  status?: string;
  docs_enabled?: boolean;
  docs_skip_reason?: string | null;
}

/** Response returned when a repo is removed from the workspace config. */
export interface WorkspaceRepoRemovedResponse {
  ok?: boolean;
  alias: string;
  remaining_repos: number;
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

export interface WorkspaceSchemaCoverage {
  total?: number;
  bound?: number;
  recovered?: number;
  shared_symbol?: number;
  unsupported_language?: number;
  non_callable?: number;
  eligible?: number;
  recovered_ratio?: number | null;
  recovered_ratio_eligible?: number | null;
}

export interface WorkspaceServiceGroup {
  id: string;
  label: string;
  source: string;
  member_count?: number;
}

/**
 * One piece of evidence behind an edge between two services.
 *
 * ``evidence`` says which of ``link``, ``package_dep`` or ``co_change`` is set.
 */
export interface WorkspaceServiceLinkEntry {
  kind: string;
  direction: "a_to_b" | "b_to_a" | "both";
  evidence: "contract" | "package" | "co_change";
  link?: WorkspaceContractLinkEntry | null;
  provider_origin?: string | null;
  consumer_origin?: string | null;
  package_dep?: WorkspacePackageDepEntry | null;
  co_change?: WorkspaceCoChangeEntry | null;
}

export interface WorkspaceServiceLinksTotal {
  kind: string;
  direction: "a_to_b" | "b_to_a" | "both";
  count: number;
}

export interface WorkspaceSymbolIdentity {
  total?: number;
  bound?: number;
  unindexed_file?: number;
  bound_ratio?: number | null;
  bound_ratio_indexed?: number | null;
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
  strategy?: string | null;
  family?: string | null;
}

/** ``GET /api/workspace/system-graph?view=map``: the graph without diagnostics. */
export interface WorkspaceSystemGraphMapResponse {
  version?: number;
  generated_at?: string;
  nodes?: WorkspaceSystemNode[];
  edges?: WorkspaceSystemMapEdge[];
  groups?: WorkspaceServiceGroup[];
}

export interface WorkspaceSystemGraphResponse {
  version?: number;
  generated_at?: string;
  nodes?: WorkspaceSystemNode[];
  edges?: WorkspaceSystemEdge[];
  diagnostics?: WorkspaceExtractionDiagnostics;
  groups?: WorkspaceServiceGroup[];
}

/** An edge of the map's lean view. Mirrors ``core.workspace.system_map_view``. */
export interface WorkspaceSystemMapEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
  match_type: string;
  confidence?: number;
  weight?: number;
  structural?: boolean;
  contract_refs?: string[] | null;
  strategy?: string | null;
  family?: string | null;
  ref_count?: number;
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
  group?: string | null;
}

/** One consumer file the join looked at, and the state it ended in. */
export interface WorkspaceTestImpactFile {
  consumer_repo?: string;
  consumer_file?: string;
  state?: string;
  measured_tests_count?: number;
  inferred_tests_count?: number;
  via?: string | null;
  provider_repos?: string[];
  contract_ids?: string[];
  consumer_symbol_ids?: string[];
}

/** ``GET /api/workspace/test-impact``: consumer tests for a provider change. */
export interface WorkspaceTestImpactResponse {
  workspace?: boolean;
  recommendations?: WorkspaceTestRecommendation[];
  recommendations_total?: number;
  recommendations_emitted?: number;
  recommendations_truncated?: boolean;
  recommendations_omitted?: number;
  recommendations_by_basis?: Record<string, number>;
  recommendations_by_repo?: Record<string, number>;
  recommendations_by_consumer_repo?: Record<string, number>;
  unresolved?: WorkspaceUnresolvedLink[];
  files_analyzed?: WorkspaceTestImpactFile[];
  summary?: Record<string, unknown>;
}

/** A test in a consumer repo that guards a changed provider file. */
export interface WorkspaceTestRecommendation {
  test_id?: string;
  test_file?: string;
  consumer_repo?: string;
  consumer_files?: string[];
  consumer_symbol_ids?: string[];
  provider_repo?: string;
  contract_ids?: string[];
  contract_types?: string[];
  basis?: string;
  via?: string;
  confidence?: number;
  source_files?: string[];
  evidence?: Record<string, unknown>[];
}

export interface WorkspaceUnmatchedConsumer {
  repo: string;
  file_path: string;
  contract_id: string;
  contract_type: string;
  reason: string;
}

/** A contract link the join could not follow, and why. */
export interface WorkspaceUnresolvedLink {
  consumer_repo?: string;
  consumer_file?: string;
  consumer_symbol_id?: string | null;
  provider_repo?: string;
  provider_file?: string;
  contract_id?: string;
  contract_type?: string;
  reason?: string;
  detail?: string | null;
}

export interface ZoomContainerResponse {
  id: string;
  name: string;
  path?: string;
  kind: string;
  technologies?: string[];
  file_count?: number;
}

export interface ZoomExternalResponse {
  id: string;
  name: string;
  display_name: string;
  kind: string;
  category: string;
  io_kind?: string | null;
  ecosystem?: string;
  file_count?: number;
  edge_count?: number;
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
  externals?: ZoomExternalResponse[];
  external_relations?: ZoomRelationResponse[];
  containers?: ZoomContainerResponse[];
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
  technologies?: string[];
  container_ids?: string[];
}

export interface ZoomRelationResponse {
  parent_id: string;
  source_id: string;
  target_id: string;
  label?: string;
  edge_count?: number;
  coupling?: string;
}
