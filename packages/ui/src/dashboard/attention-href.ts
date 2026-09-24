import { fileEntityPath } from "../shared/entity/routes";

/** Mirrors `AttentionItemType` in `core/analysis/attention/compose.py`; a Python test pins it. */
export type AttentionItemType =
  | "stale_decision"
  | "knowledge_silo"
  | "ungoverned_hotspot"
  | "dead_code"
  | "proposed_decision"
  | "health_finding"
  | "security_finding"
  | "doc_drift"
  | "refactoring";

export interface AttentionItem {
  id: string;
  type: AttentionItemType;
  title: string;
  description: string;
  /** Four levels, matching `HealthSeverity`. The three-level version this
   *  used to declare could not represent a critical health finding, which is
   *  the band that most needs to outrank everything else on the list. */
  severity: AttentionSeverity;
  /** What the item points at — a decision id, file path, owner, … Used to
   *  deep-link straight to the offending entity. */
  target_id?: string;
  /** The finding's kind within its source — a `biomarker_type`, a scanner
   *  kind, a drift kind. Resolved to a label by the renderer. */
  subtype?: string;
  href?: string;
}

/** The four-level ladder, worst first. Matches `HealthSeverity`. */
export type AttentionSeverity = "critical" | "high" | "medium" | "low";

/**
 * Sort weight for a severity, worst first.
 *
 * The server ranks the list before it ships (see
 * `core/analysis/attention/compose.py`), so nothing in the UI needs to re-sort by
 * severity. This exists for the surfaces that re-mix the order for their own
 * reasons — the dashboard panel round-robins by type — and so that they do it
 * against one ladder instead of a local literal each.
 */
export const ATTENTION_SEVERITY_RANK: Record<AttentionSeverity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

/**
 * What to call each source, for every surface that renders these items.
 *
 * One map rather than one per renderer: the panel and the Overview rows had
 * separately maintained copies that had already drifted in case ("Stale
 * Decision" against "Stale decision"), and a new source meant remembering to
 * add it to both.
 *
 * Sentence case, per the copy rules: these are labels, not headings.
 */
export const ATTENTION_TYPE_LABEL: Record<AttentionItemType, string> = {
  stale_decision: "Stale decision",
  proposed_decision: "Needs review",
  ungoverned_hotspot: "Ungoverned",
  knowledge_silo: "Knowledge silo",
  dead_code: "Dead code",
  health_finding: "Code health",
  security_finding: "Security",
  doc_drift: "Doc drift",
  refactoring: "Refactoring",
};

/**
 * The page that owns a whole source, as opposed to one item of it.
 *
 * Used for the breakdown beside the list, and as {@link getDefaultHref}'s
 * fallback when an item carries no target — those were the same URLs written
 * twice, and the copy inside the switch had already started to drift from the
 * routes.
 *
 * `decisions` is not an item type; it is the key the server uses for the three
 * decision lanes together in `attention_summary.by_source`.
 */
export function attentionSourceHref(source: string, prefix: string): string {
  switch (source) {
    // Area keys, from `attention_summary.areas`. Listed beside the item types
    // they roll up so one function answers "where does this go" for both.
    // `doc_drift` and `dead_code` name an area and an item type at once and
    // route to the same page either way, so they need no separate case.
    case "security":
      return `${prefix}/code-health?tab=security`;
    case "health":
      return `${prefix}/code-health?tab=findings`;
    case "ownership":
      return `${prefix}/owners`;
    case "stale_decision":
    case "proposed_decision":
    case "decisions":
      return `${prefix}/decisions`;
    case "knowledge_silo":
      return `${prefix}/owners`;
    case "ungoverned_hotspot":
      return `${prefix}/code-health?tab=triage`;
    case "health_finding":
      return `${prefix}/code-health?tab=findings`;
    case "refactoring":
      return `${prefix}/refactoring`;
    case "security_finding":
      return `${prefix}/code-health?tab=security`;
    case "doc_drift":
      return `${prefix}/code-health?tab=doc-drift`;
    case "dead_code":
      return `${prefix}/code-health?tab=dead-code`;
    default:
      return prefix;
  }
}

/** Area keys are not item types, so they get their own names. */
const AREA_LABEL: Record<string, string> = {
  security: "Security",
  health: "Code health",
  refactoring: "Refactoring",
  doc_drift: "Doc drift",
  decisions: "Decisions",
  ownership: "Ownership",
  dead_code: "Dead code",
};

/** What to call a `by_source` key or an area key. */
export function attentionSourceLabel(source: string): string {
  return AREA_LABEL[source] ?? ATTENTION_TYPE_LABEL[source as AttentionItemType] ?? source;
}

/**
 * Where an attention item points.
 *
 * Lives in a plain module rather than beside the panel that renders it,
 * because two surfaces need it and only one of them is a client component.
 * Importing it from `attention-panel` worked at compile time and failed at
 * runtime with "attempted to call getDefaultHref() from the server but
 * getDefaultHref is on the client" — a `"use client"` file exports components
 * to the server, not callable functions.
 *
 * Kept in one place because each branch is a per-type routing decision (a silo
 * wants the owners view filtered to its path, an ungoverned hotspot wants the
 * file page), so a second caller re-deriving them by hand gets most of them
 * wrong.
 */
export function getDefaultHref(item: AttentionItem, prefix: string): string {
  const target = item.target_id;
  switch (item.type) {
    case "stale_decision":
    case "proposed_decision":
      // Deep-link to the specific decision when we know which one.
      return target
        ? `${prefix}/decisions/${encodeURIComponent(target)}`
        : attentionSourceHref(item.type, prefix);
    case "knowledge_silo":
      // The file's own History tab, which is where ownership concentration is
      // visible. NOT `/owners?path=`: that route is a directory of people and
      // reads no path param at all, so the deep link this used to emit landed
      // on an unfiltered contributor list with the offending file nowhere on
      // it. A tab a file does not have falls back to its Overview, so this is
      // safe for any target.
      return target
        ? `${fileEntityPath(prefix, target)}?tab=history`
        : attentionSourceHref(item.type, prefix);
    case "ungoverned_hotspot":
      // Target is the hotspot's file path → open its file entity page.
      return target ? fileEntityPath(prefix, target) : attentionSourceHref(item.type, prefix);
    case "health_finding":
      // The file's Health tab, where the biomarker that produced this row is
      // listed with its deduction.
      return target
        ? `${fileEntityPath(prefix, target)}?tab=health`
        : attentionSourceHref(item.type, prefix);
    case "security_finding":
      // The category, deliberately: a security finding is a file and a line,
      // and no per-file surface renders it. Unlike the silo and dead-code
      // cases there is no better destination to deep-link to, so this lands
      // where the finding is actually readable.
      return attentionSourceHref(item.type, prefix);
    case "refactoring":
      // The plan list, not the file: a refactoring row IS the work, and the
      // page that owns it is where the plan can be read and handed to an
      // agent.
      return attentionSourceHref(item.type, prefix);
    case "doc_drift":
      // Also the category. `target_id` here is the *document* making the false
      // claim rather than the code it is wrong about, so a file page would
      // open the wrong subject.
      return attentionSourceHref(item.type, prefix);
    case "dead_code":
      // The row names one symbol, so the category list is the wrong landing:
      // the dead-code tab has no per-file filter, and arriving at a few
      // hundred unfiltered findings means finding the row again by hand. The
      // file page carries the symbol and its health. Untargeted findings still
      // get the list.
      return target ? fileEntityPath(prefix, target) : attentionSourceHref(item.type, prefix);
    default:
      return prefix;
  }
}
