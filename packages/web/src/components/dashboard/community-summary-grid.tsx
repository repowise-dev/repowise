"use client";

import { useState } from "react";
import { Network, ChevronRight, ChevronDown, ArrowRight, X } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise/ui/ui/card";
import { Badge } from "@repowise/ui/ui/badge";
import { ScrollArea } from "@repowise/ui/ui/scroll-area";
import { Skeleton } from "@repowise/ui/ui/skeleton";
import { truncatePath } from "@/lib/utils/format";
import { useCommunityDetail } from "@/lib/hooks/use-graph";
import { cn } from "@/lib/utils/cn";
import type { CommunitySummaryItem } from "@/lib/api/types";

const COMMUNITY_COLORS = [
  "bg-indigo-400", "bg-pink-400", "bg-emerald-400", "bg-amber-400",
  "bg-blue-400", "bg-purple-400", "bg-rose-400", "bg-teal-400",
  "bg-orange-400", "bg-cyan-400",
];

function CommunityDetail({ repoId, communityId, onClose }: { repoId: string; communityId: number; onClose: () => void }) {
  const { community, isLoading } = useCommunityDetail(repoId, communityId);

  if (isLoading) {
    return (
      <div className="p-4 space-y-2">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-3/4" />
      </div>
    );
  }

  if (!community) return null;

  return (
    <div className="border-t border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/50">
      <div className="flex items-center justify-between px-4 py-2 border-b border-[var(--color-border-default)]">
        <span className="text-xs font-medium text-[var(--color-text-primary)]">
          {community.label} &middot; {community.member_count} files &middot; cohesion {(community.cohesion * 100).toFixed(0)}%
        </span>
        <button onClick={onClose} className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 p-4">
        {/* Members */}
        <div>
          <p className="text-[10px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
            Members ({community.member_count})
          </p>
          <ScrollArea className="max-h-[200px]">
            <div className="space-y-1">
              {community.members.slice(0, 20).map((m) => (
                <div key={m.path} className="flex items-center justify-between gap-2 py-0.5">
                  <span className="text-[11px] font-mono text-[var(--color-text-secondary)] truncate" title={m.path}>
                    {truncatePath(m.path)}
                  </span>
                  <div className="flex items-center gap-1 shrink-0">
                    {m.is_entry_point && (
                      <Badge variant="accent" className="text-[8px] h-3.5">EP</Badge>
                    )}
                    <span className="text-[9px] font-mono text-[var(--color-text-tertiary)] tabular-nums">
                      {m.pagerank.toFixed(4)}
                    </span>
                  </div>
                </div>
              ))}
              {community.member_count > 20 && (
                <p className="text-[10px] text-[var(--color-text-tertiary)]">+{community.member_count - 20} more</p>
              )}
            </div>
          </ScrollArea>
        </div>

        {/* Neighbors */}
        {community.neighboring_communities.length > 0 && (
          <div>
            <p className="text-[10px] font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider mb-2">
              Connected Communities
            </p>
            <div className="space-y-1.5">
              {community.neighboring_communities.map((n) => (
                <div key={n.community_id} className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <ArrowRight className="h-2.5 w-2.5 shrink-0 text-[var(--color-text-tertiary)]" />
                    <span className="text-[11px] text-[var(--color-text-secondary)] truncate">{n.label}</span>
                  </div>
                  <Badge variant="outline" className="text-[9px] h-4 shrink-0">{n.cross_edge_count} edges</Badge>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

interface CommunitySummaryGridProps {
  communities: CommunitySummaryItem[];
  repoId: string;
}

export function CommunitySummaryGrid({ communities, repoId }: CommunitySummaryGridProps) {
  const [expandedId, setExpandedId] = useState<number | null>(null);

  if (communities.length === 0) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          <Network className="h-4 w-4" />
          Architecture Communities ({communities.length})
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {/* Community list */}
        <div className="divide-y divide-[var(--color-border-default)]">
          {communities.slice(0, 12).map((c, i) => (
            <div key={c.community_id}>
              <button
                onClick={() => setExpandedId(expandedId === c.community_id ? null : c.community_id)}
                className={cn(
                  "flex items-center gap-3 w-full px-4 py-2.5 text-left hover:bg-[var(--color-bg-elevated)] transition-colors",
                  expandedId === c.community_id && "bg-[var(--color-bg-elevated)]",
                )}
              >
                {/* Color dot */}
                <span className={cn("w-2.5 h-2.5 rounded-full shrink-0", COMMUNITY_COLORS[i % COMMUNITY_COLORS.length])} />

                {/* Label */}
                <span className="text-xs font-medium text-[var(--color-text-primary)] truncate min-w-0 flex-1">
                  {c.label}
                </span>

                {/* File count */}
                <span className="text-[10px] text-[var(--color-text-tertiary)] tabular-nums shrink-0">
                  {c.member_count} files
                </span>

                {/* Cohesion mini bar */}
                <div className="w-12 h-1.5 rounded-full bg-[var(--color-bg-elevated)] overflow-hidden shrink-0">
                  <div
                    className="h-full rounded-full bg-[var(--color-accent)]"
                    style={{ width: `${Math.round(c.cohesion * 100)}%` }}
                  />
                </div>

                {/* Expand arrow */}
                {expandedId === c.community_id ? (
                  <ChevronDown className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />
                ) : (
                  <ChevronRight className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]" />
                )}
              </button>

              {/* Detail panel */}
              {expandedId === c.community_id && (
                <CommunityDetail
                  repoId={repoId}
                  communityId={c.community_id}
                  onClose={() => setExpandedId(null)}
                />
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
