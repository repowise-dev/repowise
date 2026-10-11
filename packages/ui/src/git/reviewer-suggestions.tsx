"use client";

import { GitPullRequest, Sparkles } from "lucide-react";
import type { ReviewerSuggestion } from "@repowise-dev/types/modules";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Badge } from "../ui/badge";
import { Skeleton } from "../ui/skeleton";
import { OwnerAvatar } from "../owners/owner-avatar";
import { cn } from "../lib/cn";

export interface ReviewerSuggestionsProps {
  suggestions: ReviewerSuggestion[];
  isLoading?: boolean;
  /** Optional callback when a suggested reviewer is selected. */
  onSelect?: (s: ReviewerSuggestion) => void;
  /** Optional sub-title — used to mention which paths drove the suggestions. */
  subtitle?: string;
}

/**
 * Suggested reviewers for a code change, derived from `top_authors_json` and
 * `co_change_partners_json` already produced by the indexer. Renders the
 * top N with a normalized confidence bar so it's clear who is the
 * "obvious" reviewer vs. a long-tail candidate.
 */
export function ReviewerSuggestions({
  suggestions,
  isLoading,
  onSelect,
  subtitle,
}: ReviewerSuggestionsProps) {
  const max = Math.max(0.0001, ...suggestions.map((s) => s.score));

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-1.5">
          <GitPullRequest className="h-4 w-4 text-[var(--color-accent-primary)]" />
          Suggested reviewers
        </CardTitle>
        {subtitle && (
          <p className="text-xs text-[var(--color-text-tertiary)]">{subtitle}</p>
        )}
      </CardHeader>
      <CardContent className="pt-0 space-y-1.5">
        {isLoading && (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        )}

        {!isLoading && suggestions.length === 0 && (
          <p className="py-4 text-center text-xs text-[var(--color-text-tertiary)]">
            No reviewer signal for the selected paths yet.
          </p>
        )}

        {!isLoading &&
          suggestions.map((s, i) => {
            const pct = (s.score / max) * 100;
            return (
              <button
                key={s.email ?? s.name}
                onClick={() => onSelect?.(s)}
                className="flex w-full items-center gap-3 rounded-md px-2 py-1.5 text-left hover:bg-[var(--color-bg-elevated)]"
              >
                <span className="w-4 text-center text-[10px] tabular-nums text-[var(--color-text-tertiary)]">
                  {i + 1}
                </span>
                <OwnerAvatar name={s.name} email={s.email} size="sm" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-xs font-medium text-[var(--color-text-primary)]">
                      {s.name}
                    </span>
                    {i === 0 && (
                      <Badge variant="accent" className="text-[10px]">
                        <Sparkles className="mr-0.5 h-2.5 w-2.5" /> top match
                      </Badge>
                    )}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[10px] text-[var(--color-text-tertiary)]">
                    {s.recent_commits > 0 && (
                      <span>{s.recent_commits} commits / 90d</span>
                    )}
                    {s.owned_paths.length > 0 && (
                      <span>· touches {s.owned_paths.length}</span>
                    )}
                    {s.co_change_paths.length > 0 && (
                      <span>· co-changes {s.co_change_paths.length}</span>
                    )}
                  </div>
                </div>
                <div className="h-1 w-16 overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
                  <div
                    className={cn(
                      "h-full",
                      i === 0 ? "bg-[var(--color-accent-primary)]" : "bg-[var(--color-text-tertiary)]/50",
                    )}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </button>
            );
          })}
      </CardContent>
    </Card>
  );
}
