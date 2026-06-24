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
