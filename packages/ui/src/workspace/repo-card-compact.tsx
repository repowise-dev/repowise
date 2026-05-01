import { ChevronRight, Trash2 } from "lucide-react";
import { Card, CardContent } from "../ui/card";
import { Badge } from "../ui/badge";

// Slim variant of RepoCard for callers that don't have RepoStats / GitSummary
// per member yet (e.g. multi-repo workspace overviews where member detail
// would require an N+1 fan-out). Renders display name, alias, status badge,
// optional file count + visibility line, and an optional remove button.
export interface RepoCardCompactProps {
  displayName: string;
  aliasLabel?: string | null;
  status: string;
  fileCount?: number | null;
  isPublic?: boolean | null;
  href?: string | null;
  onRemove?: () => void;
  removing?: boolean;
}

export function RepoCardCompact({
  displayName,
  aliasLabel,
  status,
  fileCount,
  isPublic,
  href,
  onRemove,
  removing,
}: RepoCardCompactProps) {
  const inner = (
    <Card className="group transition-colors hover:border-[var(--color-accent-primary)]/30">
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="text-sm font-semibold text-[var(--color-text-primary)] truncate">
                {displayName}
              </h3>
              {aliasLabel && (
                <span className="text-xs text-[var(--color-text-tertiary)] truncate">
                  {aliasLabel}
                </span>
              )}
              <Badge variant="default" className="text-[10px]">{status}</Badge>
            </div>
            {(fileCount != null || isPublic != null) && (
              <p className="mt-1 text-xs text-[var(--color-text-tertiary)] truncate">
                {fileCount != null ? `${fileCount.toLocaleString()} files` : "—"}
                {isPublic != null ? ` · ${isPublic ? "Public" : "Private"}` : ""}
              </p>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {onRemove && (
              <button
                aria-label="Remove from workspace"
                type="button"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  onRemove();
                }}
                disabled={removing}
                className="p-1.5 rounded-md text-[var(--color-text-tertiary)] hover:text-red-400 hover:bg-red-500/10 transition disabled:opacity-50"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            )}
            {href && (
              <ChevronRight className="h-4 w-4 text-[var(--color-text-tertiary)] group-hover:text-[var(--color-accent-primary)]" />
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );

  if (href) return <a href={href}>{inner}</a>;
  return inner;
}
