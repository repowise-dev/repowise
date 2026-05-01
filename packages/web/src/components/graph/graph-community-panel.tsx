"use client";

import { X, ArrowRight } from "lucide-react";
import { Badge } from "@repowise-dev/ui/ui/badge";
import { ScrollArea } from "@repowise-dev/ui/ui/scroll-area";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { useCommunityDetail } from "@/lib/hooks/use-graph";
import { truncatePath } from "@repowise-dev/ui/lib/format";

interface GraphCommunityPanelProps {
  repoId: string;
  communityId: number;
  onClose: () => void;
}

export function GraphCommunityPanel({
  repoId,
  communityId,
  onClose,
}: GraphCommunityPanelProps) {
  const { community, isLoading } = useCommunityDetail(repoId, communityId);

  return (
    <div className="absolute right-0 top-0 bottom-0 w-full sm:w-[360px] border-l border-[var(--color-border-default)] bg-[var(--color-bg-surface)] z-10 flex flex-col shadow-lg shadow-black/20">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border-default)]">
        <div className="min-w-0">
          <p className="text-sm font-medium text-[var(--color-text-primary)] truncate">
            {community?.label ?? `Community ${communityId}`}
          </p>
          {community && (
            <p className="text-[10px] text-[var(--color-text-tertiary)]">
              {community.member_count} files &middot; cohesion {(community.cohesion * 100).toFixed(0)}%
            </p>
          )}
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-[var(--color-bg-elevated)] transition-colors"
        >
          <X className="h-4 w-4 text-[var(--color-text-tertiary)]" />
        </button>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="p-4 space-y-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      ) : community ? (
        <ScrollArea className="flex-1">
          <div className="p-4 space-y-4">
            {/* Members */}
            <div>
              <p className="text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
                Members ({community.member_count})
              </p>
              <div className="space-y-1">
                {community.members.map((m) => {
                  const maxPr = community.members[0]?.pagerank || 1;
                  const barWidth = maxPr > 0 ? Math.round((m.pagerank / maxPr) * 100) : 0;

                  return (
                    <div
                      key={m.path}
                      className="flex items-center gap-2 py-1 px-1.5 rounded hover:bg-[var(--color-bg-elevated)] transition-colors"
                    >
                      <div className="min-w-0 flex-1">
                        <p className="text-[11px] font-mono text-[var(--color-text-primary)] truncate" title={m.path}>
                          {truncatePath(m.path)}
                        </p>
                      </div>
                      {/* PageRank bar */}
                      <div className="w-16 h-1.5 rounded-full bg-[var(--color-bg-elevated)] overflow-hidden shrink-0">
                        <div
                          className="h-full rounded-full bg-[var(--color-accent)]"
                          style={{ width: `${barWidth}%` }}
                        />
                      </div>
                      {m.is_entry_point && (
                        <Badge variant="accent" className="text-[8px] h-3.5 shrink-0">
                          EP
                        </Badge>
                      )}
                    </div>
                  );
                })}
                {community.truncated && (
                  <p className="text-[10px] text-[var(--color-text-tertiary)] px-1.5 pt-1">
                    +{community.member_count - community.members.length} more files
                  </p>
                )}
              </div>
            </div>

            {/* Neighboring Communities */}
            {community.neighboring_communities.length > 0 && (
              <div>
                <p className="text-[11px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
                  Neighboring Communities
                </p>
                <div className="space-y-1">
                  {community.neighboring_communities.map((n) => (
                    <div
                      key={n.community_id}
                      className="flex items-center justify-between gap-2 py-1 px-1.5 rounded hover:bg-[var(--color-bg-elevated)] transition-colors"
                    >
                      <div className="flex items-center gap-1.5 min-w-0">
                        <ArrowRight className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />
                        <span className="text-[11px] text-[var(--color-text-primary)] truncate">
                          {n.label}
                        </span>
                      </div>
                      <Badge variant="outline" className="text-[9px] shrink-0 h-4">
                        {n.cross_edge_count} edges
                      </Badge>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </ScrollArea>
      ) : (
        <div className="p-4">
          <p className="text-xs text-[var(--color-text-tertiary)]">Community not found</p>
        </div>
      )}
    </div>
  );
}
