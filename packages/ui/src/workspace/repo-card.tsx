import { FileText, Flame, BarChart3, ChevronRight } from "lucide-react";
import { Card, CardContent } from "../ui/card";
import { Badge } from "../ui/badge";

// Structural subsets of RepoStats / GitSummary — the card only renders these
// three numbers, so callers without the full payloads (workspace mode) don't
// have to fabricate zero-filled fields.
interface RepoCardStats {
  file_count: number;
  doc_coverage_pct: number;
}

interface RepoCardGitSummary {
  hotspot_count: number;
}

interface RepoCardProps {
  repoId: string;
  linkPrefix?: string;
  alias: string;
  name: string;
  path: string;
  isPrimary: boolean;
  stats: RepoCardStats | null;
  gitSummary: RepoCardGitSummary | null;
  /** Workspace status — undefined in single-repo mode. */
  status?: "indexed" | "needs_index" | "missing_dir" | null;
  /** Reason docs were skipped (cost gate, no provider, index-only, etc.). */
  docsSkipReason?: string | null;
  /** Slot rendered in the card footer — typically a sync/index button. */
  actions?: React.ReactNode;
}

export function RepoCard({
  repoId,
  linkPrefix,
  alias,
  name,
  path,
  isPrimary,
  stats,
  gitSummary,
  status,
  docsSkipReason,
  actions,
}: RepoCardProps) {
  const prefix = linkPrefix ?? `/repos/${repoId}`;
  const isUnindexed = status === "needs_index" || status === "missing_dir";
  const card = (
    <Card className={`group transition-colors ${repoId ? "hover:border-[var(--color-accent-primary)]/30 cursor-pointer" : "opacity-60"}`}>
      <CardContent className="p-4">
        <div className="flex items-start justify-between mb-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="text-sm font-semibold text-[var(--color-text-primary)] truncate group-hover:text-[var(--color-accent-primary)] transition-colors">
                {name}
              </h3>
              {isPrimary && (
                <Badge variant="accent" className="text-[10px] shrink-0">
                  default
                </Badge>
              )}
              {status === "needs_index" && (
                <Badge variant="outline" className="text-[10px] shrink-0">
                  needs index
                </Badge>
              )}
              {status === "missing_dir" && (
                <Badge variant="outdated" className="text-[10px] shrink-0">
                  missing
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

        {docsSkipReason && (
          <p className="mt-3 text-xs text-[var(--color-text-tertiary)] leading-snug">
            <span className="font-medium">Docs skipped:</span> {docsSkipReason}
          </p>
        )}

        {actions && (
          <div className="mt-3 flex items-center gap-2">{actions}</div>
        )}
      </CardContent>
    </Card>
  );

  // Disable the navigation wrapper for synthetic / unindexed repos so the
  // user lands in workspace flow (sync, fix) instead of a broken
  // per-repo route.
  if (!repoId || isUnindexed) return card;

  return (
    <a href={`${prefix}/overview`}>
      {card}
    </a>
  );
}
