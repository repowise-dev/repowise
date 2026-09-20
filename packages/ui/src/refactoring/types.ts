/**
 * Shared types for the refactoring surface.
 *
 * These mirror the `/api/repos/{id}/refactoring/*` responses. The `plan`,
 * `evidence`, and `blast_radius` payloads are open per-type dicts (the backend
 * stores them as JSON); the typed accessors below describe each refactoring
 * type's shape so the plan renderer can read them without `any`.
 */

import type {
  GeneratedCode,
  RefactoringPlan,
  RefactoringType,
} from "@repowise-dev/types/refactoring";

export type {
  Confidence,
  EffortBucket,
  GeneratedCode,
  GeneratedSpan,
  RecommendationValidation,
  RecommendationValidationTarget,
  RefactoringPlan,
  RefactoringSummary,
  RefactoringTargets,
  RefactoringType,
  RefactoringTypeCount,
  ValidationBasis,
  ValidationVia,
} from "@repowise-dev/types/refactoring";

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

/** Where the shared helper should live — a directory.
 *
 *  `directory` is the only namespace a plan carries. Plans stored before that
 *  also carried `module`, a graph community label that named a directory the
 *  occurrences did not live in, so it is read last and only as a salvage for an
 *  old row with no `directory`. */
export function helperSite(plan: RefactoringPlan): string | null {
  const site = plan.plan?.suggested_site as Record<string, unknown> | undefined;
  if (!site) return null;
  const dir = typeof site.directory === "string" ? site.directory : null;
  const legacyModule = typeof site.module === "string" ? site.module : null;
  return dir ?? legacyModule;
}

export interface ExtractHelperDetail {
  /** The duplicated block's source (the anchor site, identical across sites),
   *  or `null` when the backend could not read it. */
  snippet: string | null;
  /** 1-indexed line the snippet starts on, for the gutter. */
  snippetStartLine: number | null;
  /** Whether the block was clipped at the size cap. */
  snippetTruncated: boolean;
  /** A deterministic starting name for the helper, e.g. `foo_helper`. */
  suggestedName: string | null;
}

/** The block-level detail added to an extract-helper plan: the snippet itself,
 *  a starting name, and whether it was clipped. Read defensively; every field
 *  degrades to a null/false absence rather than throwing. */
export function extractHelperDetail(plan: RefactoringPlan): ExtractHelperDetail {
  const p = (plan.plan ?? {}) as Record<string, unknown>;
  const snippet = typeof p.snippet === "string" && p.snippet.length > 0 ? p.snippet : null;
  const start = Number(p.snippet_start_line ?? 0);
  return {
    snippet,
    snippetStartLine: snippet && start > 0 ? start : null,
    snippetTruncated: p.snippet_truncated === true,
    suggestedName: typeof p.suggested_name === "string" && p.suggested_name.length > 0
      ? p.suggested_name
      : null,
  };
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

export interface ExtractMethodPlan {
  /** The line span (1-indexed, inclusive) to lift into a helper. */
  span: { start: number; end: number } | null;
  /** Inferred IN parameters and OUT return(s). */
  params: string[];
  returns: string[];
  suggested_name: string | null;
}

export function extractMethodPlan(plan: RefactoringPlan): ExtractMethodPlan {
  const p = (plan.plan ?? {}) as Record<string, unknown>;
  const rawSpan = p.span as Record<string, unknown> | undefined;
  const span =
    rawSpan && typeof rawSpan === "object"
      ? { start: Number(rawSpan.start ?? 0), end: Number(rawSpan.end ?? 0) }
      : null;
  return {
    span: span && span.start && span.end ? span : null,
    params: Array.isArray(p.params) ? (p.params as string[]) : [],
    returns: Array.isArray(p.returns) ? (p.returns as string[]) : [],
    suggested_name: typeof p.suggested_name === "string" ? p.suggested_name : null,
  };
}

export interface SplitGroup {
  name: string | null;
  symbols: string[];
  suggested_file: string | null;
}

export function splitGroups(plan: RefactoringPlan): SplitGroup[] {
  const groups = plan.plan?.groups;
  if (!Array.isArray(groups)) return [];
  return groups.map((g) => {
    const rec = (g ?? {}) as Record<string, unknown>;
    return {
      name: typeof rec.name === "string" ? rec.name : null,
      symbols: Array.isArray(rec.symbols) ? (rec.symbols as string[]) : [],
      suggested_file: typeof rec.suggested_file === "string" ? rec.suggested_file : null,
    };
  });
}

/** The shared "core" symbols that stay in the original file (spine + leftovers).
 *  Empty when the plan carries no residual group. */
export function splitResidual(plan: RefactoringPlan): string[] {
  const residual = plan.plan?.residual as Record<string, unknown> | null | undefined;
  if (!residual || typeof residual !== "object") return [];
  const symbols = residual.symbols;
  return Array.isArray(symbols) ? (symbols as string[]) : [];
}

/** Whether the original path needs a back-compat re-export shim (Python/TS/…);
 *  false for same-package languages like Go where sibling files share scope. */
export function splitShimRequired(plan: RefactoringPlan): boolean {
  return (plan.plan as Record<string, unknown>)?.shim_required === true;
}

export interface SplitBlast {
  dependent_files: string[];
  dependent_count: number;
  import_rewrites: number;
}

export function splitBlast(plan: RefactoringPlan): SplitBlast {
  const br = (plan.blast_radius ?? {}) as Record<string, unknown>;
  return {
    dependent_files: Array.isArray(br.dependent_files) ? (br.dependent_files as string[]) : [],
    dependent_count: Number(br.dependent_count ?? 0),
    import_rewrites: Number(br.import_rewrites ?? 0),
  };
}

/** A one-line synopsis for the compact card — what the plan does, at a glance. */
export interface PerformancePlanDetail {
  opportunityId: string | null;
  strategy: string;
  safety: string;
  interventionSymbol: string | null;
  affectedLocations: Array<{
    file_path: string;
    function_name?: string | null;
    line_start?: number | null;
    line_end?: number | null;
  }>;
  affectedLocationsTotal: number;
  paths: string[][];
  pathsTotal: number;
  truncated: boolean;
}

export function performancePlanDetail(plan: RefactoringPlan): PerformancePlanDetail {
  const value = plan.plan ?? {};
  const locations = Array.isArray(value.affected_locations) ? value.affected_locations : [];
  const paths = Array.isArray(value.paths) ? value.paths : [];
  return {
    opportunityId: typeof value.opportunity_id === "string" ? value.opportunity_id : null,
    strategy: typeof value.strategy === "string" ? value.strategy : "performance intervention",
    safety: typeof value.safety === "string" ? value.safety : "advisory",
    interventionSymbol:
      typeof value.intervention_symbol === "string" ? value.intervention_symbol : null,
    affectedLocations: locations.filter(
      (item): item is PerformancePlanDetail["affectedLocations"][number] =>
        Boolean(
          item &&
          typeof item === "object" &&
          typeof (item as { file_path?: unknown }).file_path === "string",
        ),
    ),
    affectedLocationsTotal: Number(value.affected_locations_total ?? locations.length),
    paths: paths.filter(
      (item): item is string[] =>
        Array.isArray(item) && item.every((node) => typeof node === "string"),
    ),
    pathsTotal: Number(value.paths_total ?? paths.length),
    truncated: value.evidence_truncated === true,
  };
}

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
    case "extract_method": {
      const em = extractMethodPlan(plan);
      const lines = em.span ? em.span.end - em.span.start + 1 : 0;
      return lines ? `Extract ${lines} line${lines === 1 ? "" : "s"} into a helper` : "Extract a helper method";
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
    case "split_file": {
      const n = splitGroups(plan).length;
      return `Split into ${n} module${n === 1 ? "" : "s"}`;
    }
    case "performance_fix": {
      const detail = performancePlanDetail(plan);
      return `${detail.strategy.replaceAll("_", " ")} at ${detail.interventionSymbol ?? plan.target_symbol}`;
    }
    default:
      return "";
  }
}

/** The files this refactoring drags along, read from whichever blast-radius
 *  shape the type carries. */
export function blastFiles(plan: RefactoringPlan): string[] {
  const br = plan.blast_radius ?? {};
  for (const key of ["files", "dependent_files"] as const) {
    const v = br[key];
    if (Array.isArray(v)) return v as string[];
  }
  return [];
}

export function blastCount(plan: RefactoringPlan): number {
  const br = plan.blast_radius ?? {};
  for (const key of ["file_count", "dependents_count", "dependent_count", "callers"] as const) {
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
  file_nloc: "File NLOC",
  symbol_count: "Symbols",
  group_count: "Groups",
  modularity: "Modularity",
  intra_edges: "Cohesive edges",
  cut_edges: "Cut edges",
  slice_nloc: "Extracted lines",
  ccn_removed: "Complexity removed",
  observations_total: "Observations",
  affected_files_total: "Affected files",
};

export function evidenceRows(plan: RefactoringPlan): { label: string; value: string }[] {
  const rows: { label: string; value: string }[] = [];
  for (const [key, label] of Object.entries(EVIDENCE_LABELS)) {
    const v = plan.evidence?.[key];
    if (typeof v === "number" && Number.isFinite(v)) {
      rows.push({
        label,
        value: Number.isInteger(v) ? String(v) : v.toFixed(2),
      });
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
 * Read a generation self-check into a single verdict for the UI. Extract Class
 * reports an LCOM4 + TCC before/after delta; Split File reports whether the
 * generated files are below the size floor and the symbols are cleanly
 * partitioned. Any skipped/absent check — and any type without a self-check —
 * returns a neutral note rather than a false pass.
 */
export function generatedVerdict(result: GeneratedCode): GeneratedVerdict | null {
  const v = result.validation;
  if (!v || typeof v !== "object") return null;
  const status = v.status;
  if (status === "skipped") {
    const reason = typeof v.reason === "string" ? v.reason : null;
    return {
      tone: "neutral",
      label: "Self-check skipped",
      ...(reason ? { detail: reason } : {}),
    };
  }
  if (status !== "checked") return null;

  if (result.refactoring_type === "extract_method") {
    const improved = v.improved === true;
    const parts: string[] = [];
    const before = fmtMetric(v.original_ccn);
    const after = fmtMetric(v.residual_ccn);
    if (before !== null && after !== null) parts.push(`CCN ${before} → ${after}`);
    const fns = fmtMetric(v.function_count);
    if (fns !== null) parts.push(`${fns} functions`);
    return {
      tone: improved ? "pass" : "fail",
      label: improved ? "Complexity reduced" : "Complexity not reduced",
      ...(parts.length ? { detail: parts.join(" · ") } : {}),
    };
  }

  if (result.refactoring_type === "split_file") {
    const improved = v.improved === true;
    const parts: string[] = [];
    const files = fmtMetric(v.file_count);
    if (files !== null) parts.push(`${files} files`);
    const maxN = fmtMetric(v.max_file_nloc);
    if (maxN !== null) parts.push(`max ${maxN} NLOC`);
    const dups = Array.isArray(v.duplicated_symbols) ? v.duplicated_symbols.length : 0;
    if (dups) parts.push(`${dups} symbol${dups === 1 ? "" : "s"} duplicated`);
    return {
      tone: improved ? "pass" : "fail",
      label: improved ? "Cleanly partitioned" : "Partition incomplete",
      ...(parts.length ? { detail: parts.join(" · ") } : {}),
    };
  }

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
    wins.push({
      hero: true,
      label: `+${plan.impact_delta.toFixed(1)} health recovered`,
    });
  }
  switch (plan.refactoring_type) {
    case "extract_class": {
      const n = extractClassGroups(plan).filter(
        (g) => g.methods.length > 0 || g.fields.length > 0,
      ).length;
      if (n)
        wins.push({
          label: `${n} focused, single-responsibility class${n === 1 ? "" : "es"}`,
        });
      break;
    }
    case "extract_helper": {
      const occ = extractHelperOccurrences(plan).length;
      const lines = Number(plan.evidence?.duplicated_lines ?? 0);
      const name = extractHelperDetail(plan).suggestedName;
      if (occ)
        wins.push({
          label: name
            ? `${occ} duplicate cop${occ === 1 ? "y" : "ies"} collapsed into ${name}()`
            : `${occ} duplicate cop${occ === 1 ? "y" : "ies"} collapsed to one`,
        });
      if (lines) wins.push({ label: `~${lines} duplicated lines removed` });
      break;
    }
    case "extract_method": {
      const em = extractMethodPlan(plan);
      const ccn = Number(plan.evidence?.ccn_removed ?? 0);
      if (em.span) {
        const lines = em.span.end - em.span.start + 1;
        wins.push({
          label: `${lines} line${lines === 1 ? "" : "s"} lifted into a focused helper`,
        });
      }
      if (ccn)
        wins.push({
          label: `-${ccn} cyclomatic complexity on the original method`,
        });
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
      if (edges)
        wins.push({
          label: `${edges} import edge${edges === 1 ? "" : "s"} cut`,
        });
      break;
    }
    case "split_file": {
      const n = splitGroups(plan).length;
      const blast = splitBlast(plan);
      if (n)
        wins.push({
          label: `${n} focused module${n === 1 ? "" : "s"} from one file`,
        });
      if (blast.import_rewrites > 0) {
        wins.push({
          label: `${blast.import_rewrites} dependent file${
            blast.import_rewrites === 1 ? "" : "s"
          } to re-point`,
        });
      } else if (blast.dependent_count > 0) {
        wins.push({
          label: `${blast.dependent_count} dependent${
            blast.dependent_count === 1 ? "" : "s"
          }, zero import edits`,
        });
      }
      break;
    }
    case "performance_fix": {
      const detail = performancePlanDetail(plan);
      wins.push({
        label: `${detail.affectedLocationsTotal} repeated call site${detail.affectedLocationsTotal === 1 ? "" : "s"} addressed at one intervention`,
      });
      break;
    }
  }
  return wins;
}

// ── Structural vs local ───────────────────────────────────────────────────

/**
 * The types that change a file's shape rather than its insides.
 *
 * This split is what the surface is organised around, and it came from the
 * distribution rather than from taste: on a real index Extract Helper and
 * Extract Method are 96% of all plans and 89% of everything is rated small
 * effort, so "all plans" is overwhelmingly a list of local tidy-ups you do
 * while you are already in the file. The rest change how the codebase is laid
 * out, are worth planning, and are few enough to rank by hand.
 */
export const STRUCTURAL_TYPES: readonly RefactoringType[] = [
  "split_file",
  "break_cycle",
  "extract_class",
  "move_method",
];

export function isStructural(plan: RefactoringPlan): boolean {
  return (STRUCTURAL_TYPES as readonly string[]).includes(plan.refactoring_type);
}

/** A plan's coordinates on the structural map, or null when either figure is
 *  missing. 0 means "not measured" for both — a repo with no health pass has no
 *  line counts, and plotting those at the origin would invent a cluster. */
export function planPoint(plan: RefactoringPlan): { x: number; y: number } | null {
  const x = plan.dependents ?? 0;
  const y = plan.file_nloc ?? 0;
  return x > 0 && y > 0 ? { x, y } : null;
}

/**
 * Below this many plottable points the map is not worth its height: a scatter
 * with four dots asks the reader to decode two axes to learn less than four
 * rows would tell them. The section falls back to rows only.
 */
export const MAP_MIN_POINTS = 8;

/**
 * The evidence behind a plan, as one sentence.
 *
 * `planSynopsis` says what the plan *is* ("Split into 6 modules"). This says
 * why it was proposed, in the numbers the detector actually recorded, because
 * the ranked plans at the top of the page are the ones a reader has to decide
 * about rather than skim. Returns "" when the evidence dict is empty, so a
 * caller renders nothing rather than an empty clause.
 */
export function planReason(plan: RefactoringPlan): string {
  const ev = plan.evidence ?? {};
  const num = (key: string): number => Number(ev[key] ?? 0);

  switch (plan.refactoring_type) {
    case "performance_fix": {
      const detail = performancePlanDetail(plan);
      const rationale = typeof plan.evidence?.rationale === "string" ? plan.evidence.rationale : "";
      return `${detail.safety === "proven" ? "Proven" : "Advisory"} ${detail.strategy.replaceAll("_", " ")} across ${detail.affectedLocationsTotal} affected call site${detail.affectedLocationsTotal === 1 ? "" : "s"}.${rationale ? ` ${rationale}` : ""}`;
    }
    case "split_file": {
      const parts: string[] = [];
      const nloc = num("file_nloc");
      const symbols = num("symbol_count");
      const groups = num("group_count") || splitGroups(plan).length;
      if (nloc && symbols && groups) {
        parts.push(
          `${nloc.toLocaleString()} lines and ${symbols} symbols that fall into ${groups} groups the imports already respect`,
        );
      }
      const cut = num("cut_edges");
      const intra = num("intra_edges");
      if (intra) {
        parts.push(
          cut === 0
            ? `No edges cross a seam`
            : `${cut} of ${intra} internal edges cross a seam`,
        );
      }
      const cochange = num("cochange_edges");
      if (cochange) parts.push(`${cochange} pairs of these symbols keep changing together`);
      return parts.join(". ") + (parts.length ? "." : "");
    }
    case "break_cycle": {
      const size = num("cycle_size") || cycleMembers(plan).length;
      const edges = num("edge_count");
      const cuts = num("cut_count") || cutEdges(plan).length;
      if (!size) return "";
      return `${size} modules import each other in a ring${
        edges ? ` across ${edges} edges` : ""
      }. ${cuts} cut${cuts === 1 ? "" : "s"} open${cuts === 1 ? "s" : ""} it.`;
    }
    case "extract_class": {
      const methods = num("method_count");
      const fields = num("field_count");
      const lcom = num("lcom4");
      if (!methods) return "";
      return `${methods} methods and ${fields} fields in one class, splitting into ${
        lcom || 2
      } groups that share no state.`;
    }
    case "move_method": {
      const foreign = num("foreign_calls");
      const own = num("own_calls");
      if (!foreign) return "";
      return `Reaches into the other class ${foreign} times against ${own} call${
        own === 1 ? "" : "s"
      } to its own.`;
    }
    case "extract_method": {
      const slice = num("slice_nloc");
      const ccn = num("ccn_removed");
      if (!slice) return "";
      return `${slice} lines doing one separable job${
        ccn ? `, carrying ${ccn} of the function's decision points` : ""
      }.`;
    }
    case "extract_helper": {
      const occ = num("occurrence_count") || extractHelperOccurrences(plan).length;
      const lines = num("duplicated_lines");
      if (!occ) return "";
      return `The same ${lines ? `${lines}-line ` : ""}block appears at ${occ} sites.`;
    }
    default:
      return "";
  }
}
