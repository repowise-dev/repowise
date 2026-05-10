import type { HealthScoreComponent } from "@repowise-dev/ui/dashboard/health-score-ring";

export interface HealthScoreResult {
  score: number;
  components: HealthScoreComponent[];
  /** True when the repo was indexed without generating wiki docs. */
  indexOnly: boolean;
  /** Human-readable note describing any reweighting (or undefined if none). */
  note?: string;
}

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
}): HealthScoreResult {
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

  // Detect index-only mode: docs were never generated for this repo, so doc
  // coverage and freshness are structurally zero. Penalizing them would punish
  // users who explicitly opted into `repowise init --index-only`. We drop both
  // components and renormalize the remaining weights.
  const docsGenerated = docCoveragePct > 0 || freshnessScore > 0;

  // Doc coverage: 0-100, weight 25% (when applicable)
  const docScore = Math.min(docCoveragePct, 100);

  // Freshness: 0-100, weight 25% (when applicable)
  const freshScore = Math.min(freshnessScore * 100, 100);

  // Dead code: lower is better, weight 20%
  const deadRatio = symbolCount > 0 ? deadExportCount / symbolCount : 0;
  const deadScore = Math.max(0, (1 - deadRatio * 5) * 100);

  // Hotspot density: lower is better, weight 15%
  const hotspotRatio = totalFiles > 0 ? hotspotCount / totalFiles : 0;
  const hotspotScore = Math.max(0, (1 - hotspotRatio * 3) * 100);

  // Knowledge distribution: fewer silos is better, weight 15%
  const siloRatio = totalModules > 0 ? siloCount / totalModules : 0;
  const siloScore = Math.max(0, (1 - siloRatio * 2) * 100);

  const baseComponents: HealthScoreComponent[] = docsGenerated
    ? [
        {
          key: "doc_coverage",
          label: "Doc Coverage",
          weight: 0.25,
          score: Math.round(docScore),
          detail: `${Math.round(docCoveragePct)}% of symbols documented`,
        },
        {
          key: "freshness",
          label: "Freshness",
          weight: 0.25,
          score: Math.round(freshScore),
          detail: "Based on doc-to-code drift analysis",
        },
        {
          key: "dead_code",
          label: "Dead Code",
          weight: 0.2,
          score: Math.round(deadScore),
          detail: `${deadExportCount} dead exports out of ${symbolCount} symbols`,
        },
        {
          key: "hotspot_density",
          label: "Hotspot Density",
          weight: 0.15,
          score: Math.round(hotspotScore),
          detail: `${hotspotCount} hotspots across ${totalFiles} files`,
        },
        {
          key: "knowledge_silos",
          label: "Knowledge Silos",
          weight: 0.15,
          score: Math.round(siloScore),
          detail: `${siloCount} silo${siloCount === 1 ? "" : "s"} out of ${totalModules} modules`,
        },
      ]
    : [
        // Index-only: redistribute the 0.25+0.25 doc weights across the
        // remaining three components proportionally (0.20:0.15:0.15 → 0.40:0.30:0.30).
        {
          key: "dead_code",
          label: "Dead Code",
          weight: 0.4,
          score: Math.round(deadScore),
          detail: `${deadExportCount} dead exports out of ${symbolCount} symbols`,
        },
        {
          key: "hotspot_density",
          label: "Hotspot Density",
          weight: 0.3,
          score: Math.round(hotspotScore),
          detail: `${hotspotCount} hotspots across ${totalFiles} files`,
        },
        {
          key: "knowledge_silos",
          label: "Knowledge Silos",
          weight: 0.3,
          score: Math.round(siloScore),
          detail: `${siloCount} silo${siloCount === 1 ? "" : "s"} out of ${totalModules} modules`,
        },
      ];

  const composite = Math.round(
    baseComponents.reduce((sum, c) => sum + c.score * c.weight, 0),
  );

  return {
    score: Math.max(0, Math.min(100, composite)),
    components: baseComponents,
    indexOnly: !docsGenerated,
    note: docsGenerated
      ? undefined
      : "Docs haven't been generated for this repo (index-only). Doc Coverage and Freshness are excluded; remaining components are reweighted.",
  };
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
