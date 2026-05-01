import { FileText, Flame, BarChart3, ChevronRight } from "lucide-react";
import { Card, CardContent } from "../ui/card";
import { Badge } from "../ui/badge";
import type { RepoStats } from "@repowise/types/workspace";
import type { GitSummary } from "@repowise/types/git";

interface RepoCardProps {
  repoId: string;
  alias: string;
  name: string;
  path: string;
  isPrimary: boolean;
  stats: RepoStats | null;
  gitSummary: GitSummary | null;
}

export function RepoCard({
  repoId,
  alias,
  name,
  path,
  isPrimary,
  stats,
  gitSummary,
}: RepoCardProps) {
  const card = (
    <Card className={`group transition-colors ${repoId ? "hover:border-[var(--color-accent-primary)]/30 cursor-pointer" : "opacity-60"}`}>
      <CardContent className="p-4">
        <div className="flex items-start justify-between mb-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-[var(--color-text-primary)] truncate group-hover:text-[var(--color-accent-primary)] transition-colors">
                {name}
              </h3>
              {isPrimary && (
                <Badge variant="accent" className="text-[10px] shrink-0">
                  default
                </Badge>
              )}
            </div>
            <p className="text-xs text-[var(--color-text-tertiary)] font-mono mt-0.5 truncate" title={`${alias} · ${path}`}>
              {alias} &middot; {path}
            </p>
          </div>
          {repoId && (
            <ChevronRight className="h-4 w-4 text-[var(--color-text-tertiary)] shrink-0 opacity-0 group-hover:opacity-100 transition-opacity mt-0.5" />
          )}
        </div>

        <div className="grid grid-cols-3 gap-2">
          <div className="flex items-center gap-1.5">
            <FileText className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
            <span className="text-xs text-[var(--color-text-secondary)]">
              {stats?.file_count ?? "—"} files
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <BarChart3 className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
            <span className="text-xs text-[var(--color-text-secondary)]">
              {stats ? `${Math.round(stats.doc_coverage_pct)}% docs` : "—"}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <Flame className="h-3.5 w-3.5 text-[var(--color-text-tertiary)]" />
            <span className="text-xs text-[var(--color-text-secondary)]">
              {gitSummary?.hotspot_count ?? "—"} hotspots
            </span>
          </div>
        </div>
      </CardContent>
    </Card>
  );

  if (!repoId) return card;

  return (
    <a href={`/repos/${repoId}/overview`}>
      {card}
    </a>
  );
}
