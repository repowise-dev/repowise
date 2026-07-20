import { HardDrive } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { formatBytes, formatPercent } from "../lib/format";

export interface IndexStorageMiniData {
  /** Total on-disk size of the repo's `.repowise/` directory. */
  index_storage_bytes: number;
  /** Wiki page count — used for a secondary line when docs exist. */
  page_count?: number;
  /**
   * Average doc *confidence* as 0–100. Named `doc_coverage_pct` for the wire
   * contract, but it is a mean page confidence, not a share of files covered —
   * so it is labelled as confidence here rather than coverage.
   */
  doc_coverage_pct?: number;
}

interface IndexStorageMiniProps {
  data: IndexStorageMiniData;
}

/**
 * Compact overview tile for local index footprint — mirrors the
 * `repowise status` storage row from the CLI.
 */
export function IndexStorageMini({ data }: IndexStorageMiniProps) {
  const hasDocs = (data.page_count ?? 0) > 0;
  const confidence =
    data.doc_coverage_pct != null ? formatPercent(data.doc_coverage_pct / 100) : null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <HardDrive className="h-4 w-4 text-[var(--color-text-tertiary)]" />
          Index storage
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="space-y-1">
          <span className="text-3xl font-semibold tabular-nums text-[var(--color-text-primary)]">
            {formatBytes(data.index_storage_bytes)}
          </span>
          <p className="text-xs text-[var(--color-text-tertiary)]">
            wiki.db + vectors on disk
            {hasDocs && confidence ? ` · ${confidence} avg doc confidence` : ""}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
