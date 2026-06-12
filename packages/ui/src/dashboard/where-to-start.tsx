import { Compass } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { formatNumber, truncatePath } from "../lib/format";
import { fileEntityPath } from "../shared/entity/routes";

export interface WhereToStartTarget {
  path: string;
  pagerank: number;
  doc_words: number;
}

interface WhereToStartCardProps {
  /** Onboarding targets: high-centrality files with the least documentation. */
  targets: WhereToStartTarget[];
  repoId: string;
  linkPrefix?: string;
  previewCount?: number;
}

/**
 * "Where do I start reading?" — the onboarding-targets list promoted from
 * the old Overview Ownership tab. Rows link to the canonical file page.
 */
export function WhereToStartCard({
  targets,
  repoId,
  linkPrefix,
  previewCount = 5,
}: WhereToStartCardProps) {
  const prefix = linkPrefix ?? `/repos/${repoId}`;
  if (targets.length === 0) return null;
  const shown = targets.slice(0, previewCount);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Compass className="h-4 w-4 text-[var(--color-accent-primary)]" />
          Where to start
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <p className="text-[11px] text-[var(--color-text-tertiary)] mb-2">
          Central files with the least documentation — high-leverage reading.
        </p>
        <ul className="space-y-1">
          {shown.map((t) => (
            <li key={t.path}>
              <a
                href={fileEntityPath(prefix, t.path)}
                className="block -mx-2 px-2 py-1 rounded hover:bg-[var(--color-bg-elevated)] transition-colors"
              >
                <p className="text-[11px] font-mono text-[var(--color-text-primary)] truncate">
                  {truncatePath(t.path, 44)}
                </p>
                <p className="text-[10px] text-[var(--color-text-tertiary)]">
                  pagerank {t.pagerank.toFixed(4)} · {formatNumber(t.doc_words)} doc words
                </p>
              </a>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
