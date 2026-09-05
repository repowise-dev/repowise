/**
 * Canonical decision-record types.
 *
 * Canonical source: engine `DecisionRecordResponse`. Some downstream backends
 * emit a leaner `DecisionEntry` shape that omits `repository_id` and types
 * `status`/`source` as bare `string` instead of literal unions — consumer
 * adapters fill defaults before passing data to components.
 */

// ---------------------------------------------------------------------------
// Shared vocabulary
//
// The engine owns these words. Every one below is pinned against
// `tests/fixtures/decision_vocabulary.json`, which the Python registry
// generates, by `__tests__/decisions-vocabulary.test.ts` and by
// `tests/unit/analysis/test_decision_vocabulary_contract.py`. Adding a source or a
// currency on either side without the other fails a build.
//
// Four surfaces used to keep their own copies, and they had already drifted:
// this union named a retired `readme_mining` and omitted five live sources,
// two status ladders disagreed about where `superseded` sat, and the retired
// set was hand-mirrored with nothing tying it to its source.
// ---------------------------------------------------------------------------

/**
 * `decision_records.status`, best first: a rule the team stands behind, then a
 * candidate, then history, then a tombstone. This is the sort order, not just
 * a set.
 *
 * `dismissed` is a tombstone and `deprecated` is a retirement, and the
 * difference is load-bearing: the engine skips a dismissed record on every
 * re-extraction and hides it from listings, where a deprecated one keeps being
 * re-derived and keeps its row.
 */
export const DECISION_STATUSES = [
  "active",
  "proposed",
  "superseded",
  "deprecated",
  "dismissed",
] as const;

export type DecisionStatus = (typeof DECISION_STATUSES)[number];

/** The word for a status. Presentation, so the fixture pins coverage only. */
export const DECISION_STATUS_LABELS: Record<DecisionStatus, string> = {
  active: "Active",
  proposed: "Proposed",
  superseded: "Superseded",
  deprecated: "Deprecated",
  dismissed: "Dismissed",
};

/** Sort key for a status. An unknown status sorts after every known one. */
export function decisionStatusRank(status: string): number {
  const i = (DECISION_STATUSES as readonly string[]).indexOf(status);
  return i === -1 ? DECISION_STATUSES.length : i;
}

/**
 * The values `DecisionRecord.source` can carry on a live index.
 *
 * Wider than the capture-source registry the settings panel switches on and
 * off: `commit` and `llm_inferred` are stored provenance with no toggle, and
 * broad session discovery stores `session` rather than a value of its own.
 */
export const DECISION_SOURCES = [
  "adr",
  "cli",
  "comment",
  "commit",
  "git_archaeology",
  "inline_marker",
  "llm_inferred",
  "pr",
  "session",
] as const;

export type DecisionSource = (typeof DECISION_SOURCES)[number];

/**
 * Sources the engine no longer emits and whose rows were purged with them. A
 * record carrying one cannot exist, so offering it as a filter is a control
 * that cannot act. `code_comment` is retired and `comment` is not: they are
 * different values and a live index still carries the second.
 */
export const RETIRED_DECISION_SOURCES = [
  "code_comment",
  "readme_mining",
  "changelog",
] as const;

export type RetiredDecisionSource = (typeof RETIRED_DECISION_SOURCES)[number];

/**
 * Column labels for every source a row can carry, retired ones included so an
 * old record still renders a word rather than a raw key.
 *
 * Presentation, so it is not in the fixture. The test asserts it covers every
 * live and retired source, which is the drift that actually happened: one
 * column mixed "Docs" with a bare `pr`.
 */
export const DECISION_SOURCE_LABELS: Record<
  DecisionSource | RetiredDecisionSource,
  string
> = {
  adr: "ADR",
  cli: "Manual",
  comment: "Comment",
  commit: "Commit",
  git_archaeology: "Git history",
  inline_marker: "Marker",
  llm_inferred: "Model",
  pr: "Pull request",
  session: "Session",
  changelog: "Changelog",
  code_comment: "Comment",
  readme_mining: "Docs",
};

/**
 * `source` is an unconstrained string on the wire, so a plain index into the
 * label map reaches `Object.prototype`: a row sourced `toString` would hand a
 * function to `localeCompare` and throw inside a sort comparator.
 */
export function decisionSourceLabel(source: string): string {
  return Object.hasOwn(DECISION_SOURCE_LABELS, source)
    ? DECISION_SOURCE_LABELS[source as DecisionSource]
    : source;
}

/** Whether a source string names a retired source. */
export function isRetiredDecisionSource(source: string): boolean {
  return (RETIRED_DECISION_SOURCES as readonly string[]).includes(source);
}

/**
 * What an accepted decision's authority currently amounts to.
 *
 * Not the same axis as `DecisionStatus`, which is the legacy projection kept
 * in step for readers that predate the acceptance split. `needs_review` and
 * `uncheckable` are derived from the code rather than set by a person, which
 * is why a record can be stored `active` and shown `uncheckable`.
 */
export const DECISION_CURRENCIES = [
  "active",
  "needs_review",
  "uncheckable",
  "superseded",
  "dismissed",
] as const;

export type DecisionCurrency = (typeof DECISION_CURRENCIES)[number];

/**
 * The word for a currency, and the sentence that says why it reads that way.
 *
 * `uncheckable` and `needs_review` are the two a reader cannot guess: the
 * first means the decision names nothing the repository can be asked about,
 * the second that the files it names have moved. Neither is an error, and
 * neither is quiet.
 */
export const DECISION_CURRENCY_LABELS: Record<DecisionCurrency, string> = {
  active: "Active",
  needs_review: "Needs review",
  uncheckable: "Uncheckable",
  superseded: "Superseded",
  dismissed: "Dismissed",
};

export const DECISION_CURRENCY_DESCRIPTIONS: Record<DecisionCurrency, string> =
  {
    active: "Accepted, and still describes the code it names.",
    needs_review: "Accepted, but the code it names has moved since.",
    uncheckable:
      "Accepted, but it names no file or module, so nothing checks it against the code.",
    superseded: "Replaced by a later decision.",
    dismissed: "Authority withdrawn. Kept for history.",
  };

/** Currencies that still bind future work. A moved decision is one to re-read. */
export const GOVERNING_CURRENCIES: readonly DecisionCurrency[] = [
  "active",
  "needs_review",
];

/** Where a candidate is in review. `dismissed` is a durable tombstone. */
export const CANDIDATE_REVIEW_STATES = [
  "open",
  "accepted",
  "merged",
  "needs_split",
  "dismissed",
] as const;

export type CandidateReviewState = (typeof CANDIDATE_REVIEW_STATES)[number];

/** Derived granularity of a decision's blast area, narrowest first. */
export type DecisionScope = "file" | "module" | "cross-module";

export interface DecisionRecord {
  id: string;
  repository_id: string;
  title: string;
  status: DecisionStatus;
  context: string;
  decision: string;
  rationale: string;
  alternatives: string[];
  consequences: string[];
  affected_files: string[];
  affected_modules: string[];
  tags: string[];
  /**
   * Where the record came from. Includes the retired values, because a row
   * written before a source was retired still carries its name and still has
   * to render; only the *filter* vocabulary is narrowed to the live set.
   */
  source: DecisionSource | RetiredDecisionSource;
  evidence_commits: string[];
  evidence_file: string | null;
  evidence_line: number | null;
  confidence: number;
  staleness_score: number;
  superseded_by: string | null;
  last_code_change: string | null;
  /** Trust tier of the decision's primary supporting evidence. Optional for back-compat. */
  verification?: DecisionVerification;
  /**
   * Derived granularity level. Optional for back-compat with older backends;
   * null when the record has no code linkage at all.
   */
  scope?: DecisionScope | null;
  created_at: string;
  updated_at: string;
  /**
   * Effective currency from the acceptance, or null for a candidate.
   *
   * This is the authority answer. `status` is the projection kept in step for
   * readers that predate the acceptance split, so the two are different
   * questions: a record can be stored `active` and carry no currency at all,
   * which is exactly what a candidate is. Optional for back-compat with
   * backends that predate the field; absent and null both mean unknown, so a
   * surface that needs the distinction should ask for the lane instead.
   */
  currency?: DecisionCurrency | null;
  /** Number of evidence rows backing the record. List endpoint only. */
  evidence_count?: number | null;
  /** Top-ranked evidence row, slimmed for list rows. List endpoint only. */
  evidence_preview?: EvidencePreview | null;
}

/** The top-ranked evidence row, slimmed for decision list rows. */
export interface EvidencePreview {
  source: string;
  source_quote: string;
  verification: DecisionVerification;
  evidence_file?: string | null;
  evidence_line?: number | null;
}

export interface DecisionCreateInput {
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

/**
 * PATCH body for /api/repos/{id}/decisions/{decision_id}. All fields are
 * optional — clients can update just the status, just the linkage, or both.
 */
export interface DecisionStatusUpdate {
  status?: DecisionStatus;
  superseded_by?: string;
  affected_modules?: string[];
  affected_files?: string[];
}

export interface DecisionHealth {
  summary: {
    active: number;
    proposed: number;
    deprecated: number;
    superseded: number;
    stale: number;
  };
  stale_decisions: DecisionRecord[];
  proposed_awaiting_review: DecisionRecord[];
  ungoverned_hotspots: string[];
}

// ---------------------------------------------------------------------------
// Phase 4C: evidence / lineage / decision-graph
// ---------------------------------------------------------------------------

/**
 * Trust level of a decision's supporting evidence. `exact` = the source quote
 * was found verbatim in the cited file/commit; `fuzzy` = a near-match;
 * `unverified` = the quote could not be located (LLM-derived, treat with care).
 */
export type DecisionVerification = "exact" | "fuzzy" | "unverified";

/** One supporting evidence row for a decision. */
export interface DecisionEvidence {
  id: string;
  source: string;
  source_rank: number;
  evidence_file: string | null;
  evidence_line: number | null;
  evidence_commit: string | null;
  source_quote: string;
  confidence: number;
  verification: DecisionVerification;
  created_at: string;
}

/**
 * One hop in a decision's supersession/evolution chain. The chain is ordered
 * root -> current; `relation` describes how the NEWER decision related to this
 * one ("supersedes" | "refines" | null for the terminal/current entry).
 */
export interface DecisionLineageEntry {
  id: string;
  title: string;
  status: DecisionStatus;
  source: string;
  relation: string | null;
}

export interface DecisionGraphNode {
  id: string;
  title: string;
  status: DecisionStatus;
  source: string;
  confidence: number;
  staleness_score: number;
  verification: DecisionVerification;
}

export type DecisionEdgeKind =
  | "supersedes"
  | "refines"
  | "relates_to"
  | "conflicts_with";

export interface DecisionGraphEdge {
  src: string;
  dst: string;
  kind: DecisionEdgeKind;
  confidence: number;
  evidence: string;
}

export interface DecisionCodeEdge {
  decision_id: string;
  node_id: string;
  link_type: "file" | "module";
}

export interface DecisionGraph {
  nodes: DecisionGraphNode[];
  decision_edges: DecisionGraphEdge[];
  code_edges: DecisionCodeEdge[];
}

/**
 * Review lanes, as the list endpoint's `lane` parameter accepts them.
 *
 * `governing` is the roll-up of `active` and `needs_review`, so it overlaps
 * the two and is meant for a caller asking "what binds", not for a tab row.
 */
export const DECISION_LANE_FILTERS = [
  "candidates",
  "governing",
  "active",
  "needs_review",
  "uncheckable",
  "history",
] as const;

export type DecisionLaneFilter = (typeof DECISION_LANE_FILTERS)[number];

/**
 * Records per review lane.
 *
 * `candidates` and the four currency lanes partition the repository and sum to
 * `total`; `governing` is the roll-up of the two that still bind.
 */
export interface DecisionLaneCounts {
  candidates: number;
  active: number;
  needs_review: number;
  uncheckable: number;
  history: number;
  governing: number;
  total: number;
}

/**
 * The review lanes, in tab order. They partition a repository: every record is
 * in exactly one, and the five counts sum to the total.
 *
 * `governing` is deliberately absent. It is the roll-up of `active` and
 * `needs_review`, so it overlaps two lanes; it lives in `DECISION_LANE_FILTERS`
 * for a caller asking "what binds" and never in a tab row, because a tab row of
 * overlapping datasets is one a reader cannot add up.
 */
export const DECISION_LANES = [
  "active",
  "candidates",
  "needs_review",
  "uncheckable",
  "history",
] as const;

export type DecisionLane = (typeof DECISION_LANES)[number];

/** The capture presets a caller may set. `custom` is a reading, not a choice. */
export const DECISION_PRESETS = [
  "default",
  "off",
  "local_only",
  "balanced",
  "full",
] as const;

/**
 * What `record_acceptance` would refuse this record for, mirrored so a review
 * surface can disable a control it knows will be refused instead of finding out
 * from the error toast. Pinned against the Python contract by the vocabulary
 * tests. The fourth check, accepter-or-artifact identity, is not here: the
 * reviewer supplies that at the moment they act, so it is never something the
 * record is missing. `record_blockers` on the Python side asks the same way.
 *
 * `selfAuthored` is the exemption for a record somebody typed: they wrote the
 * claim rather than reviewing an inference, so the entry is its own provenance.
 * The engine grants it to `source === "cli"` when an accepter is known.
 */
export function decisionAcceptanceBlockers(record: {
  rationale?: string;
  decision?: string;
  affected_files?: string[];
  affected_modules?: string[];
  evidence_commits?: string[];
  evidence_file?: string | null;
  source?: string;
}): string[] {
  const blockers: string[] = [];
  const nonBlank = (values: (string | null | undefined)[]) =>
    values.some((v) => (v ?? "").trim().length > 0);

  if (!nonBlank([record.rationale, record.decision])) {
    blockers.push("no rationale or explicit constraint reason");
  }
  if (
    !nonBlank(record.affected_files ?? []) &&
    !nonBlank(record.affected_modules ?? [])
  ) {
    blockers.push("no scope: name the files or modules it governs");
  }
  const selfAuthored = record.source === "cli";
  if (
    !selfAuthored &&
    !nonBlank(record.evidence_commits ?? []) &&
    !nonBlank([record.evidence_file])
  ) {
    blockers.push("no evidence reference");
  }
  return blockers;
}

/** Counts by status, from a grouped COUNT on the server. */
export interface DecisionCounts {
  total: number;
  active: number;
  proposed: number;
  superseded: number;
  deprecated: number;
}

/** One capture source's capabilities and resolved state. */
export interface DecisionSourceState {
  key: string;
  label: string;
  description: string;
  /** `machine` for inferred capture, `human` for authority routes. */
  authority: "machine" | "human";
  deterministic: boolean;
  supports_llm: boolean;
  /** False for authority routes, which have no capture to switch off. */
  togglable: boolean;
  enabled: boolean;
  llm_enabled: boolean;
  status:
    | "enabled"
    | "disabled"
    | "deterministic_only"
    | "skipped_no_provider"
    | "always_on";
  reason: string;
}

export type DecisionPreset =
  | "default"
  | "off"
  | "local_only"
  | "balanced"
  | "full"
  | "custom";

/** Per-update ceiling on the one broad session-discovery call. */
export interface DecisionDiscoveryBudget {
  max_sessions: number;
  max_input_tokens: number;
}

/** A change to the discovery budget. Omitted fields keep their value. */
export type DecisionDiscoveryPatch = Partial<DecisionDiscoveryBudget>;

/** The resolved decision capture policy for one repository. */
export interface DecisionSettings {
  enabled: boolean;
  llm: boolean;
  preset: DecisionPreset;
  discovery: DecisionDiscoveryBudget;
  sources: DecisionSourceState[];
  provider_available: boolean;
  warnings: string[];
  /** Legacy config keys still honoured. A save replaces them. */
  legacy_keys: string[];
  /** Pass back on write to detect a concurrent edit. */
  etag: string;
}

/** A change to one source. Omitted fields keep their current value. */
export interface DecisionSourcePatch {
  enabled?: boolean;
  llm?: boolean;
}

/** A partial policy write. Omitted fields keep their current value. */
export interface DecisionSettingsUpdate {
  enabled?: boolean;
  llm?: boolean;
  preset?: Exclude<DecisionPreset, "custom">;
  sources?: Record<string, DecisionSourcePatch>;
  discovery?: DecisionDiscoveryPatch;
  etag?: string;
}
