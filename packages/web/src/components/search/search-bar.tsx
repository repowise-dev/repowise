"use client";

import { Search } from "lucide-react";
import { Input } from "@repowise/ui/ui/input";
import { cn } from "@/lib/utils/cn";

type SearchType = "fulltext" | "semantic";

interface SearchBarProps {
  query: string;
  onQueryChange: (q: string) => void;
  searchType: SearchType;
  onSearchTypeChange: (t: SearchType) => void;
  className?: string;
}

export function SearchBar({
  query,
  onQueryChange,
  searchType,
  onSearchTypeChange,
  className,
}: SearchBarProps) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="relative flex-1">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--color-text-tertiary)]" />
        <Input
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder="Search wiki pages…"
          className="pl-9"
          autoFocus
        />
      </div>
      <div className="flex rounded-md border border-[var(--color-border-default)] overflow-hidden text-xs">
        {(["fulltext", "semantic"] as SearchType[]).map((t) => (
          <button
            key={t}
            onClick={() => onSearchTypeChange(t)}
            className={cn(
              "px-3 py-1.5 font-medium transition-colors capitalize",
              searchType === t
                ? "bg-[var(--color-accent-primary)] text-[var(--color-text-inverse)]"
                : "bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)]",
            )}
          >
            {t}
          </button>
        ))}
      </div>
    </div>
  );
}
