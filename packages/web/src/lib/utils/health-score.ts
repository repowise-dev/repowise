/** Compute composite health score from available repo data */
export function computeHealthScore(params: {
  docCoveragePct: number;
  freshnessScore: number;
  deadExportCount: number;
  symbolCount: number;
  hotspotCount: number;
  totalFiles: number;
  siloCount: number;
  totalModules: number;
}): number {
  const {
    docCoveragePct,
    freshnessScore,
    deadExportCount,
    symbolCount,
    hotspotCount,
    totalFiles,
    siloCount,
    totalModules,
  } = params;

  // Doc coverage: 0-100, weight 25%
  const docScore = Math.min(docCoveragePct, 100);

  // Freshness: 0-100, weight 25%
  const freshScore = Math.min(freshnessScore * 100, 100);

  // Dead code: lower is better, weight 20%
  const deadRatio = symbolCount > 0 ? deadExportCount / symbolCount : 0;
  const deadScore = Math.max(0, (1 - deadRatio * 5) * 100); // 20% dead = 0 score

  // Hotspot density: lower is better, weight 15%
  const hotspotRatio = totalFiles > 0 ? hotspotCount / totalFiles : 0;
  const hotspotScore = Math.max(0, (1 - hotspotRatio * 3) * 100);

  // Knowledge distribution: fewer silos is better, weight 15%
  const siloRatio = totalModules > 0 ? siloCount / totalModules : 0;
  const siloScore = Math.max(0, (1 - siloRatio * 2) * 100);

  const composite = Math.round(
    docScore * 0.25 +
    freshScore * 0.25 +
    deadScore * 0.2 +
    hotspotScore * 0.15 +
    siloScore * 0.15
  );

  return Math.max(0, Math.min(100, composite));
}

/** Aggregate language distribution from graph nodes */
export function aggregateLanguages(
  nodes: Array<{ language: string; node_type: string }>
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const n of nodes) {
    if (n.node_type === "file" || !n.node_type) {
      const lang = n.language?.toLowerCase() || "unknown";
      counts[lang] = (counts[lang] || 0) + 1;
    }
  }
  return counts;
}

export type { AttentionItem } from "@repowise-dev/ui/dashboard/attention-panel";
import type { AttentionItem } from "@repowise-dev/ui/dashboard/attention-panel";

/** Build attention items from existing API responses */
export function buildAttentionItems(params: {
  staleDecisions: Array<{ id: string; title: string; staleness_score: number }>;
  proposedDecisions: Array<{ id: string; title: string }>;
  ungovernedHotspots: string[];
  siloModules: Array<{ module_path: string; primary_owner: string | null }>;
  deadCodeSafe: Array<{ id: string; file_path: string; symbol_name: string | null; lines: number }>;
}): AttentionItem[] {
  const items: AttentionItem[] = [];

  for (const d of params.staleDecisions.slice(0, 3)) {
    items.push({
      id: `stale-${d.id}`,
      type: "stale_decision",
      title: d.title,
      description: `Staleness score: ${Math.round(d.staleness_score * 100)}%`,
      severity: d.staleness_score > 0.7 ? "high" : "medium",
    });
  }

  for (const h of params.ungovernedHotspots.slice(0, 3)) {
    items.push({
      id: `hotspot-${h}`,
      type: "ungoverned_hotspot",
      title: h.split("/").slice(-2).join("/"),
      description: "High churn, no governing decision",
      severity: "high",
    });
  }

  for (const s of params.siloModules.slice(0, 3)) {
    items.push({
      id: `silo-${s.module_path}`,
      type: "knowledge_silo",
      title: s.module_path,
      description: `Single owner: ${s.primary_owner ?? "unknown"}`,
      severity: "medium",
    });
  }

  const totalDeadLines = params.deadCodeSafe.reduce((sum, d) => sum + d.lines, 0);
  if (params.deadCodeSafe.length > 0) {
    items.push({
      id: "dead-code-summary",
      type: "dead_code",
      title: `${params.deadCodeSafe.length} safe-to-delete findings`,
      description: `${totalDeadLines} lines can be removed`,
      severity: totalDeadLines > 500 ? "medium" : "low",
    });
  }

  for (const p of params.proposedDecisions.slice(0, 2)) {
    items.push({
      id: `proposed-${p.id}`,
      type: "proposed_decision",
      title: p.title,
      description: "Awaiting review",
      severity: "low",
    });
  }

  // Sort by severity
  const order = { high: 0, medium: 1, low: 2 };
  items.sort((a, b) => order[a.severity] - order[b.severity]);

  return items;
}
