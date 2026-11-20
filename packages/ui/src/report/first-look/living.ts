import type {
  FirstLookLivingItem,
  FirstLookLivingReport,
  FirstLookItemStatus,
} from "@repowise-dev/types/first-look";
import { formatNumber } from "../../lib/format";

export type LivingSection = FirstLookLivingReport["sections"][number];

/** Statuses a person or an agent set that close an item. */
const CLOSED: ReadonlySet<FirstLookItemStatus> = new Set(["done", "resolved", "false_positive"]);

export const isClosed = (item: FirstLookLivingItem): boolean => CLOSED.has(item.status);

/** How far the repository has come since the first look, for the progress line. */
export interface Progress {
  resolved: number;
  acknowledged: number;
  added: number;
  /** Every item the bar is a share of: what is open now plus what was resolved. */
  total: number;
}

export function progress(report: FirstLookLivingReport): Progress {
  const items = report.sections.flatMap((s) => s.items);
  const resolved = (report.baseline?.resolved.length ?? 0) + items.filter(isClosed).length;
  return {
    resolved,
    acknowledged: items.filter((i) => i.status === "acknowledged").length,
    added: items.filter((i) => i.change === "new").length,
    total: items.length + (report.baseline?.resolved.length ?? 0),
  };
}

/** The strip's number for a section: what the section counts, with its unit where it is a share. */
export function stripValue(section: LivingSection): string {
  const facts = section.facts;
  if (section.key === "tests" && typeof facts.line_pct === "number") return `${Math.round(facts.line_pct)}%`;
  if (section.key === "dead_code") {
    const safe = typeof facts.safe_exports === "number" ? facts.safe_exports : 0;
    const files = typeof facts.unreachable_files === "number" ? facts.unreachable_files : 0;
    if (safe + files > 0) return formatNumber(safe + files);
  }
  if (section.key === "security") {
    const open = section.items.filter((i) => !isClosed(i));
    return formatNumber(open.reduce((n, i) => n + Math.max(i.locations_total, 1), 0));
  }
  return formatNumber(section.items.length);
}

/** How a section lays out: most are a list; a few read better as a compact table. */
export type SectionLayout = "list" | "functions" | "dead_code" | "documents" | "coverage";

export function sectionLayout(key: string): SectionLayout {
  switch (key) {
    case "health":
      return "functions";
    case "dead_code":
      return "dead_code";
    case "docs":
      return "documents";
    case "tests":
      return "coverage";
    default:
      return "list";
  }
}

/** Sections small enough to sit side by side on a wide screen. */
export const PAIRED: readonly [string, string] = ["tests", "hygiene"];

/** A number from an item's facts, or null when core did not record one. */
export function factNumber(item: FirstLookLivingItem, key: string): number | null {
  const v = item.title_facts[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** The status pill an item shows, if any: new beats open, and open shows nothing. */
export function pillOf(item: FirstLookLivingItem): FirstLookItemStatus | "new" | null {
  if (item.status !== "open") return item.status;
  return item.change === "new" ? "new" : null;
}

/** The baseline items gone since the first look, by the section they sat in. */
export function resolvedBySection(report: FirstLookLivingReport): Map<string, FirstLookLivingItem[]> {
  const out = new Map<string, FirstLookLivingItem[]>();
  for (const item of report.baseline?.resolved ?? []) {
    const list = out.get(item.section) ?? [];
    list.push(item);
    out.set(item.section, list);
  }
  return out;
}
