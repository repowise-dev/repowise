import { Boxes, Lightbulb, Waypoints, FileCode, Repeat, Network } from "lucide-react";
import type { StatsHighlights } from "@repowise-dev/types/stats";
import { StatCallout, SuperlativesGrid } from "@repowise-dev/ui/stats";
import { LanguageDonut } from "@repowise-dev/ui/dashboard/language-donut";
import { formatNumber } from "@repowise-dev/ui/lib/format";

export function ArchitectureTab({ data }: { data: StatsHighlights }) {
  const { scale, knowledge, quality, superlatives } = data;
  const graph = data.graph;

  const langDistribution: Record<string, number> = {};
  for (const l of scale.languages) langDistribution[l.language] = l.file_count;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <StatCallout
          label="Modules"
          value={formatNumber(scale.module_count)}
          icon={<Boxes className="h-4 w-4" />}
          sub={`${formatNumber(scale.file_count)} files`}
        />
        <StatCallout
          label="Entry points"
          value={formatNumber(scale.entry_point_count)}
          icon={<Waypoints className="h-4 w-4" />}
          sub="reachable roots in the graph"
        />
        <StatCallout
          label="Symbols"
          value={formatNumber(scale.symbol_count)}
          icon={<FileCode className="h-4 w-4" />}
          sub={`${formatNumber(scale.language_count)} languages · ${Math.round(
            quality.doc_coverage_pct,
          )}% documented`}
        />
        <StatCallout
          label="Decisions"
          value={formatNumber(knowledge.decision_count)}
          icon={<Lightbulb className="h-4 w-4" />}
          sub={`${formatNumber(knowledge.active_decision_count)} active`}
        />
        {graph && (
          <StatCallout
            label="Dependency cycles"
            value={formatNumber(graph.cycle_clusters)}
            tone={graph.cycle_clusters > 0 ? "warning" : "success"}
            icon={<Repeat className="h-4 w-4" />}
            sub={
              graph.cycle_clusters > 0
                ? `largest spans ${formatNumber(graph.largest_cycle)} files · ${formatNumber(
                    graph.files_in_cycles,
                  )} caught in a loop`
                : "no circular imports — clean layering"
            }
          />
        )}
        {graph && (
          <StatCallout
            label="Neighborhoods"
            value={formatNumber(graph.community_count)}
            icon={<Network className="h-4 w-4" />}
            sub="natural clusters in the import graph"
          />
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <LanguageDonut distribution={langDistribution} />
        <div className="lg:col-span-2">
          <h3 className="mb-2 text-sm font-semibold text-[var(--color-text-primary)]">
            Structural records
          </h3>
          <SuperlativesGrid superlatives={superlatives} />
        </div>
      </div>
    </div>
  );
}
