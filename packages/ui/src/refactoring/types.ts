/**
 * Shared types for the refactoring surface.
 *
 * These mirror the `/api/repos/{id}/refactoring/*` responses. The `plan`,
 * `evidence`, and `blast_radius` payloads are open per-type dicts (the backend
 * stores them as JSON); the typed accessors below describe each refactoring
 * type's shape so the plan renderer can read them without `any`.
 */

export type RefactoringType =
  | "extract_class"
  | "extract_helper"
  | "move_method"
  | "break_cycle";

export type EffortBucket = "S" | "M" | "L" | "XL";
export type Confidence = "low" | "medium" | "high";

export interface RefactoringPlan {
  id: string;
  refactoring_type: RefactoringType | string;
  file_path: string;
  target_symbol: string;
  line_start: number | null;
  line_end: number | null;
  plan: Record<string, unknown>;
  evidence: Record<string, unknown>;
  impact_delta: number;
  effort_bucket: EffortBucket | string;
  blast_radius: Record<string, unknown>;
  confidence: Confidence | string;
  source_biomarker: string;
  rank_score: number;
}

export interface RefactoringTypeCount {
  type: string;
  count: number;
}

export interface RefactoringSummary {
  total: number;
  by_type: RefactoringTypeCount[];
}

export interface RefactoringTargets {
  summary: RefactoringSummary;
  plans: RefactoringPlan[];
}

// ── Per-type plan shapes (the open `plan` dict, read defensively) ──────────

export interface ExtractClassGroup {
  name: string | null;
  methods: string[];
  fields: string[];
}

export interface ExtractHelperOccurrence {
  file: string;
  line_start: number;
  line_end: number;
}

export interface CutEdge {
  from: string;
  to: string;
}

export function extractClassGroups(plan: RefactoringPlan): ExtractClassGroup[] {
  const groups = plan.plan?.groups;
  if (!Array.isArray(groups)) return [];
  return groups.map((g) => {
    const rec = (g ?? {}) as Record<string, unknown>;
    return {
      name: typeof rec.name === "string" ? rec.name : null,
      methods: Array.isArray(rec.methods) ? (rec.methods as string[]) : [],
      fields: Array.isArray(rec.fields) ? (rec.fields as string[]) : [],
    };
  });
}

export function extractHelperOccurrences(plan: RefactoringPlan): ExtractHelperOccurrence[] {
  const occ = plan.plan?.occurrences;
  if (!Array.isArray(occ)) return [];
  return occ.map((o) => {
    const rec = (o ?? {}) as Record<string, unknown>;
    return {
      file: String(rec.file ?? ""),
      line_start: Number(rec.line_start ?? 0),
      line_end: Number(rec.line_end ?? 0),
    };
  });
}

export function helperSite(plan: RefactoringPlan): string | null {
  const site = plan.plan?.suggested_site as Record<string, unknown> | undefined;
  if (!site) return null;
  const module = typeof site.module === "string" ? site.module : null;
  const dir = typeof site.directory === "string" ? site.directory : null;
  return module ?? dir;
}

export interface MoveTarget {
  method: string;
  from_class: string;
  to_class: string;
  to_file: string | null;
}

export function moveTarget(plan: RefactoringPlan): MoveTarget | null {
  const p = plan.plan as Record<string, unknown>;
  if (!p || typeof p.method !== "string") return null;
  return {
    method: p.method,
    from_class: String(p.from_class ?? ""),
    to_class: String(p.to_class ?? ""),
    to_file: typeof p.to_file === "string" ? p.to_file : null,
  };
}

export function cycleMembers(plan: RefactoringPlan): string[] {
  const cycle = plan.plan?.cycle;
  return Array.isArray(cycle) ? (cycle as string[]) : [];
}

export function cutEdges(plan: RefactoringPlan): CutEdge[] {
  const edges = plan.plan?.cut_edges;
  if (!Array.isArray(edges)) return [];
  return edges.map((e) => {
    const rec = (e ?? {}) as Record<string, unknown>;
    return { from: String(rec.from ?? ""), to: String(rec.to ?? "") };
  });
}

/** A one-line synopsis for the compact card — what the plan does, at a glance. */
export function planSynopsis(plan: RefactoringPlan): string {
  switch (plan.refactoring_type) {
    case "extract_class": {
      const n = extractClassGroups(plan).filter(
        (g) => g.methods.length > 0 || g.fields.length > 0,
      ).length;
      return `Split into ${n} cohesive class${n === 1 ? "" : "es"}`;
    }
    case "extract_helper": {
      const occ = extractHelperOccurrences(plan);
      const lines = Number(plan.evidence?.duplicated_lines ?? 0);
      return `${occ.length} duplicate${occ.length === 1 ? "" : "s"}${
        lines ? ` · ${lines} lines` : ""
      }`;
    }
    case "move_method": {
      const mv = moveTarget(plan);
      return mv ? `${mv.from_class} → ${mv.to_class}` : "Move method";
    }
    case "break_cycle": {
      const members = cycleMembers(plan).length;
      const edges = cutEdges(plan).length;
      return `${members} files · cut ${edges} edge${edges === 1 ? "" : "s"}`;
    }
    default:
      return "";
  }
}

/** The files this refactoring drags along, read from whichever blast-radius
 *  shape the type carries. */
export function blastFiles(plan: RefactoringPlan): string[] {
  const files = plan.blast_radius?.files;
  return Array.isArray(files) ? (files as string[]) : [];
}

export function blastCount(plan: RefactoringPlan): number {
  const br = plan.blast_radius ?? {};
  for (const key of ["file_count", "dependents_count", "callers"] as const) {
    const v = br[key];
    if (typeof v === "number" && v) return v;
  }
  return blastFiles(plan).length;
}

// ── Evidence + win framing (shared by the inspector and the modal) ─────────

export const EVIDENCE_LABELS: Record<string, string> = {
  lcom4: "LCOM4",
  method_count: "Methods",
  field_count: "Fields",
  wmc: "WMC",
  occurrence_count: "Occurrences",
  duplicated_lines: "Duplicated lines",
  co_change_count: "Co-changes",
  foreign_calls: "Calls to target",
  own_calls: "Calls to own class",
  own_distance: "Distance to own",
  target_distance: "Distance to target",
  cycle_size: "Cycle size",
  edge_count: "Edges in cycle",
  cut_count: "Edges to cut",
};

export function evidenceRows(plan: RefactoringPlan): { label: string; value: string }[] {
  const rows: { label: string; value: string }[] = [];
  for (const [key, label] of Object.entries(EVIDENCE_LABELS)) {
    const v = plan.evidence?.[key];
    if (typeof v === "number" && Number.isFinite(v)) {
      rows.push({ label, value: Number.isInteger(v) ? String(v) : v.toFixed(2) });
    }
  }
  return rows;
}

export interface PlanWin {
  /** A health-score win is rendered as the hero; the rest are supporting. */
  hero?: boolean;
  label: string;
}

// ── Generated code (the opt-in LLM enrichment result) ─────────────────────

export interface GeneratedSpan {
  file: string;
  line_start: number;
  line_end: number;
}

/**
 * The result of the "Generate code" action — mirrors the backend
 * `GenerateCodeResponse` (POST `…/refactoring/{id}/generate-code`). `diff` is a
 * unified diff; `validation` is the per-type self-check (open dict, read
 * defensively via {@link generatedVerdict}).
 */
export interface GeneratedCode {
  suggestion_id: string | null;
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
  validation: Record<string, unknown>;
  spans: GeneratedSpan[];
}

export type VerdictTone = "pass" | "fail" | "neutral";

export interface GeneratedVerdict {
  tone: VerdictTone;
  /** Short headline, e.g. "Cohesion improved" / "Self-check skipped". */
  label: string;
  /** Optional supporting detail (the metric deltas, or the skip reason). */
  detail?: string;
}

function fmtMetric(value: unknown): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

/**
 * Read the Extract Class self-check (LCOM4 + TCC before/after) into a single
 * verdict for the UI. Other types — and any skipped/absent check — return a
 * neutral note rather than a false pass.
 */
export function generatedVerdict(result: GeneratedCode): GeneratedVerdict | null {
  const v = result.validation;
  if (!v || typeof v !== "object") return null;
  const status = v.status;
  if (status === "skipped") {
    const reason = typeof v.reason === "string" ? v.reason : null;
    return { tone: "neutral", label: "Self-check skipped", ...(reason ? { detail: reason } : {}) };
  }
  if (status !== "checked") return null;

  const improved = v.improved === true;
  const parts: string[] = [];
  const beforeL = fmtMetric(v.before_lcom4);
  const afterL = fmtMetric(v.after_max_lcom4);
  if (beforeL !== null && afterL !== null) parts.push(`LCOM4 ${beforeL} → ${afterL}`);
  const beforeT = fmtMetric(v.before_tcc);
  const afterT = fmtMetric(v.after_min_tcc);
  if (beforeT !== null && afterT !== null) parts.push(`TCC ${beforeT} → ${afterT}`);
  const classes = fmtMetric(v.class_count);
  if (classes !== null) parts.push(`${classes} classes`);

  return {
    tone: improved ? "pass" : "fail",
    label: improved ? "Cohesion improved" : "Cohesion not improved",
    ...(parts.length ? { detail: parts.join(" · ") } : {}),
  };
}

/** The concrete payoff of applying a plan, framed as wins for the "what you
 *  gain" band. The health delta (if any) leads as the hero. */
export function planWins(plan: RefactoringPlan): PlanWin[] {
  const wins: PlanWin[] = [];
  if (plan.impact_delta > 0) {
    wins.push({ hero: true, label: `+${plan.impact_delta.toFixed(1)} health recovered` });
  }
  switch (plan.refactoring_type) {
    case "extract_class": {
      const n = extractClassGroups(plan).filter(
        (g) => g.methods.length > 0 || g.fields.length > 0,
      ).length;
      if (n) wins.push({ label: `${n} focused, single-responsibility class${n === 1 ? "" : "es"}` });
      break;
    }
    case "extract_helper": {
      const occ = extractHelperOccurrences(plan).length;
      const lines = Number(plan.evidence?.duplicated_lines ?? 0);
      if (occ) wins.push({ label: `${occ} duplicate cop${occ === 1 ? "y" : "ies"} collapsed to one` });
      if (lines) wins.push({ label: `~${lines} duplicated lines removed` });
      break;
    }
    case "move_method": {
      const mv = moveTarget(plan);
      if (mv) wins.push({ label: `${mv.method} lives with the data it uses` });
      break;
    }
    case "break_cycle": {
      const members = cycleMembers(plan).length;
      const edges = cutEdges(plan).length;
      if (members) wins.push({ label: `${members} files untangled` });
      if (edges) wins.push({ label: `${edges} import edge${edges === 1 ? "" : "s"} cut` });
      break;
    }
  }
  return wins;
}
