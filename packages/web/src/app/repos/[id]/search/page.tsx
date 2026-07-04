"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { Search } from "lucide-react";
import { SearchBar } from "@/components/search/search-bar";
import { SearchResultCard } from "@/components/search/search-result-card";
import { EmptyState } from "@repowise-dev/ui/shared/empty-state";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { useSearch } from "@/lib/hooks/use-search";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";

type SearchType = "fulltext" | "semantic";

export default function SearchPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [query, setQuery] = useState("");
  const [searchType, setSearchType] = useState<SearchType>("semantic");
  const [scope, setScope] = useState<"repo" | "workspace">("repo");

  // Synthetic ws:<alias> IDs point at unindexed workspace entries — the
  // backend short-circuits search on those. Skip the repo_id filter and
  // gently fall back to workspace scope so the user still sees results.
  const isSynthetic = id?.startsWith("ws:");
  const effectiveRepoId =
    scope === "repo" && !isSynthetic ? id : undefined;

  const { results, isLoading, isTyping, error } = useSearch(query, {
    search_type: searchType,
    limit: 20,
    repo_id: effectiveRepoId,
  });

  const showResults = query.trim().length >= 2;
  const loading = (isLoading || isTyping) && showResults;

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-3xl">
      <div>
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
          <Search className="h-5 w-5 text-[var(--color-accent-primary)]" />
          Search
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Full-text and semantic search across {scope === "repo" ? "this repo" : "the whole workspace"}.
        </p>
      </div>

      <SearchBar
        query={query}
        onQueryChange={setQuery}
        searchType={searchType}
        onSearchTypeChange={setSearchType}
      />

      <div className="flex items-center gap-2 text-xs">
        <span className="text-[var(--color-text-tertiary)]">Scope:</span>
        <button
          type="button"
          onClick={() => setScope("repo")}
          disabled={isSynthetic}
          className={
            "rounded-md border px-2 py-1 transition-colors " +
            (scope === "repo" && !isSynthetic
              ? "border-[var(--color-accent-primary)] bg-[var(--color-accent-primary)]/10 text-[var(--color-text-primary)]"
              : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] disabled:opacity-50")
          }
          title={isSynthetic ? "This repo isn't indexed yet — workspace scope used." : undefined}
        >
          This repo
        </button>
        <button
          type="button"
          onClick={() => setScope("workspace")}
          className={
            "rounded-md border px-2 py-1 transition-colors " +
            (scope === "workspace" || isSynthetic
              ? "border-[var(--color-accent-primary)] bg-[var(--color-accent-primary)]/10 text-[var(--color-text-primary)]"
              : "border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]")
          }
        >
          Workspace
        </button>
      </div>

      {showResults && error && (
        <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-4 text-sm text-[var(--color-outdated)]">
          Couldn&apos;t run search: {toFriendlyMessage(error)}
        </div>
      )}

      {!showResults ? (
        <EmptyState
          title="Start typing to search"
          description="Search across titles, content, and file paths."
          icon={<Search className="h-8 w-8" />}
        />
      ) : loading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-20 w-full rounded-lg" />
          ))}
        </div>
      ) : results.length === 0 ? (
        <EmptyState
          title="No results"
          description={`No pages matched "${query}". Try different keywords or switch search mode.`}
        />
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-[var(--color-text-tertiary)]">
            {results.length} result{results.length !== 1 ? "s" : ""} for &ldquo;{query}&rdquo;
          </p>
          {results.map((r) => (
            <SearchResultCard key={r.page_id} result={r} query={query} repoId={id} />
          ))}
        </div>
      )}
    </div>
  );
}
