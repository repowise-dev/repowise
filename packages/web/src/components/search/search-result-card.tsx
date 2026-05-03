import Link from "next/link";
import { Badge } from "@repowise-dev/ui/ui/badge";
import { truncatePath } from "@repowise-dev/ui/lib/format";
import { cn } from "@/lib/utils/cn";
import type { SearchResultResponse } from "@/lib/api/types";

interface SearchResultCardProps {
  result: SearchResultResponse;
  query: string;
  repoId: string;
}

function highlightSnippet(snippet: string, query: string): React.ReactNode {
  if (!query.trim()) return snippet;
  const terms = query.trim().split(/\s+/).filter(Boolean);
  const escaped = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  const regex = new RegExp(`(${escaped})`, "gi");
  // split with capturing group yields alternating non-match / match / non-match ...
  const parts = snippet.split(regex);
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <strong key={i} className="text-[var(--color-text-primary)] font-semibold">
        {part}
      </strong>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

export function SearchResultCard({ result, query, repoId }: SearchResultCardProps) {
  const href = `/repos/${repoId}/wiki/${encodeURIComponent(result.page_id)}`;

  return (
    <Link
      href={href}
      className="block rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4 hover:border-[var(--color-border-hover)] hover:bg-[var(--color-bg-elevated)] transition-colors"
    >
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <h3 className="text-sm font-medium text-[var(--color-text-primary)] truncate" title={result.title}>
          {result.title}
        </h3>
        <div className="flex items-center gap-1.5 shrink-0">
          <Badge variant="default">{result.page_type.replace(/_/g, " ")}</Badge>
          <span
            className={cn(
              "inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium tabular-nums",
              result.score >= 0.8
                ? "border-green-500/20 bg-green-500/10 text-green-500"
                : result.score >= 0.5
                  ? "border-yellow-500/20 bg-yellow-500/10 text-yellow-500"
                  : "border-[var(--color-border-default)] bg-transparent text-[var(--color-text-tertiary)]",
            )}
          >
            {Math.round(result.score * 100)}%
          </span>
        </div>
      </div>
      <p className="text-xs font-mono text-[var(--color-text-tertiary)] mb-2" title={result.target_path}>
        {truncatePath(result.target_path, 60)}
      </p>
      {result.snippet && (
        <p className="text-xs text-[var(--color-text-secondary)] line-clamp-2 leading-relaxed">
          {highlightSnippet(result.snippet, query)}
        </p>
      )}
    </Link>
  );
}
