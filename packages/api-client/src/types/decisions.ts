// ---------------------------------------------------------------------------
// Decisions
// ---------------------------------------------------------------------------

// One vocabulary, imported rather than re-typed. Ten shapes were re-declared
// here and had drifted from the canonical ones: `source` still named a retired
// `readme_mining` and omitted five live values, `DecisionStatusUpdate.status`
// had widened to a bare string, and `DecisionCounts` was a byte-identical
// duplicate. A client that re-types its own wire contract is a second contract.
export type {
  CandidateReviewState,
  DecisionCodeEdge,
  DecisionCounts,
  DecisionCurrency,
  DecisionEvidence,
  DecisionGraph,
  DecisionGraphEdge,
  DecisionGraphNode,
  DecisionLaneCounts,
  DecisionLaneFilter,
  DecisionLineageEntry,
  DecisionPreset,
  DecisionScope,
  DecisionSettings,
  DecisionSettingsUpdate,
  DecisionSource,
  DecisionSourcePatch,
  DecisionSourceState,
  DecisionStatus,
  DecisionStatusUpdate,
  DecisionVerification,
  EvidencePreview,
} from "@repowise-dev/types/decisions";

// The record under the name this client has always used for it. `Response` is
// the transport-layer name; the shape is the canonical one.
export type { DecisionRecord as DecisionRecordResponse } from "@repowise-dev/types/decisions";
export type { DecisionCreateInput as DecisionCreate } from "@repowise-dev/types/decisions";

import type {
  DecisionEvidence,
  DecisionLineageEntry,
} from "@repowise-dev/types/decisions";

// Envelope shapes, which exist only on the wire and have no canonical
// counterpart: the endpoints wrap their lists in a single-key object.
export interface DecisionEvidenceResponse {
  evidence: DecisionEvidence[];
}

export interface DecisionLineageResponse {
  lineage: DecisionLineageEntry[];
}
