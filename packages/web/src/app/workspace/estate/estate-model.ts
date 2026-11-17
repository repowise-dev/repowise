/**
 * Reads the estate report document for the page. Every figure comes from a
 * check the engine wrote (`report/sections/estate_*.py`); nothing here
 * recomputes a section, it only picks checks and joins their per-repo rows.
 */
import type {
  EstateCheck,
  EstateHealthRow,
  EstateHistoryRow,
  EstateReport,
} from "@repowise-dev/types/estate";
import type { GraphEvidence } from "@repowise-dev/types/workspace";

/** The check *name* in section *sectionId*, if the report carries it. */
export function findCheck(
  report: EstateReport,
  sectionId: string,
  name: string,
): EstateCheck | undefined {
  const section = (report.sections ?? []).find((s) => s.id === sectionId);
  return (section?.checks ?? []).find((c) => c.name === name);
}

/** A check's `data[key]` as the shape the engine writes there, or `fallback`. */
export function checkData<T>(check: EstateCheck | undefined, key: string, fallback: T): T {
  const value = check?.data?.[key];
  return value === undefined || value === null ? fallback : (value as T);
}

/**
 * The item every graph section gives when no system graph is stored: the
 * summary then holds one inconclusive `estate` check saying which command
 * builds it.
 */
export function missingGraph(report: EstateReport): EstateCheck | null {
  return findCheck(report, "estate-summary", "estate") ?? null;
}

/** The graph's evidence state, as the summary's `evidence` check carries it. */
export function graphEvidence(report: EstateReport): GraphEvidence | null {
  const check = findCheck(report, "estate-summary", "evidence");
  return (check?.data as GraphEvidence | null | undefined) ?? null;
}

export interface EstateRepoRow {
  repo: string;
  indexed: boolean;
  indexState: "behind" | "unreadable" | null;
  health: EstateHealthRow | null;
  /** Null when the repo's history was not measured (too short, unread, not indexed). */
  churn: EstateHistoryRow | null;
  magnets: EstateHistoryRow | null;
}

/**
 * One row per configured repository, joining the health, churn-hotspot and
 * bug-magnet checks. Repos keep the health check's order (lowest health
 * first), then the rest in report order, so the weakest repo leads.
 */
export function repoRows(report: EstateReport): EstateRepoRow[] {
  const health = checkData<EstateHealthRow[]>(
    findCheck(report, "estate-health", "health"),
    "repositories",
    [],
  );
  const churn = checkData<Record<string, EstateHistoryRow>>(
    findCheck(report, "estate-health", "churn-hotspots"),
    "repositories",
    {},
  );
  const magnets = checkData<Record<string, EstateHistoryRow>>(
    findCheck(report, "estate-health", "bug-magnets"),
    "repositories",
    {},
  );
  const byRepo = new Map(health.map((h) => [h.repo, h]));
  const aliases = (report.repos ?? []).map((r) => ({
    repo: r.alias ?? r.name,
    indexed: r.indexed ?? true,
    indexState: r.index_state ?? null,
  }));
  const rank = (repo: string) => {
    const i = health.findIndex((h) => h.repo === repo);
    return i < 0 ? health.length : i;
  };
  return aliases
    .map((a, order) => ({ ...a, order }))
    .sort((a, b) => rank(a.repo) - rank(b.repo) || a.order - b.order)
    .map(({ repo, indexed, indexState }) => ({
      repo,
      indexed,
      indexState,
      health: byRepo.get(repo) ?? null,
      churn: churn[repo] ?? null,
      magnets: magnets[repo] ?? null,
    }));
}
