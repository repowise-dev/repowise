import { Boxes, Lightbulb, Waypoints, FileCode } from "lucide-react";
import type { StatsHighlights } from "@repowise-dev/types/stats";
import { StatCallout, SuperlativesGrid } from "@repowise-dev/ui/stats";
import { LanguageDonut } from "@repowise-dev/ui/dashboard/language-donut";
import { formatNumber } from "@repowise-dev/ui/lib/format";

export function ArchitectureTab({ data }: { data: StatsHighlights }) {
  const { scale, knowledge, quality, superlatives } = data;

  const langDistribution: Record<string, number> = {};
  for (const l of scale.languages) langDistribution[l.language] = l.file_count;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
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
          label="Decisions"
          value={formatNumber(knowledge.decision_count)}
          icon={<Lightbulb className="h-4 w-4" />}
          sub={`${formatNumber(knowledge.active_decision_count)} active`}
        />
        <StatCallout
          label="Symbols"
          value={formatNumber(scale.symbol_count)}
          icon={<FileCode className="h-4 w-4" />}
          sub={`${formatNumber(scale.language_count)} languages · ${Math.round(
            quality.doc_coverage_pct,
          )}% documented`}
        />
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
