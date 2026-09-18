"use client";

/**
 * The drill-down: one row per drifted assertion.
 *
 * Column order is the reading order of the sentence the row makes: *this
 * document*, at *this line*, claims *this thing* exists, and here is why we
 * think it does not. The document leads because it is the file the reader
 * opens; putting the target first would read as a list of broken code files,
 * which is the opposite of what a finding says.
 */

import { FileText } from "lucide-react";
import {
  docDriftConfidenceTier,
  docDriftKindLabel,
  type DocDriftFinding,
} from "@repowise-dev/types/doc-drift";

import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "../shared/responsive-table";
import { EmptyState } from "../shared/empty-state";

export interface DriftFindingsTableProps {
  findings: DocDriftFinding[];
  /** Where a document link goes; the row is clickable when given. */
  documentHref: (path: string, line?: number) => string;
  navigate?: ((href: string) => void) | undefined;
}

/**
 * Colour is not the name of the tier. Critical and high need labels because
 * amber alone is not a sufficient word, and the same holds here: the tier is
 * written out and the colour only reinforces it.
 */
const TIER_CLS: Record<string, string> = {
  high: "text-[var(--color-warning)]",
  medium: "text-[var(--color-text-secondary)]",
  low: "text-[var(--color-text-tertiary)]",
};

const TIER_LABEL: Record<string, string> = {
  high: "Near-certain",
  medium: "Medium",
  low: "Low",
};

export function DriftFindingsTable({
  findings,
  documentHref,
  navigate,
}: DriftFindingsTableProps) {
  const open = (finding: DocDriftFinding) => {
    navigate?.(documentHref(finding.file_path, finding.line_number));
  };

  const columns: ResponsiveColumn<DocDriftFinding>[] = [
    {
      key: "document",
      header: "Document",
      priority: 1,
      // Widths are capped per cell, not left to the table: a repo-relative
      // path and a heading slug are both long and unbreakable, and an
      // auto-laid-out table gives them the room by pushing the confidence
      // column off the side, which is the one column a reader triages on.
      cellClassName: "max-w-[34ch]",
      render: (f) => (
        <div className="flex min-w-0 flex-col gap-0.5">
          <span
            className="truncate font-mono text-xs text-[var(--color-text-primary)]"
            title={`${f.file_path}:${f.line_number}`}
          >
            {f.file_path}
            <span className="text-[var(--color-text-tertiary)]">:{f.line_number}</span>
          </span>
          <span
            className="truncate text-xs text-[var(--color-text-tertiary)]"
            title={f.reason}
          >
            {f.reason}
          </span>
        </div>
      ),
    },
    {
      key: "target",
      header: "Claims this exists",
      mobileLabel: "Claims",
      priority: 1,
      cellClassName: "max-w-[34ch]",
      render: (f) => (
        <span
          className="block truncate font-mono text-xs text-[var(--color-text-secondary)]"
          title={f.target}
        >
          {f.target}
        </span>
      ),
    },
    {
      key: "kind",
      header: "Reference",
      priority: 2,
      render: (f) => (
        <span className="text-xs text-[var(--color-text-secondary)]">
          {docDriftKindLabel(f.kind)}
        </span>
      ),
    },
    {
      key: "confidence",
      header: "Confidence",
      align: "right",
      priority: 2,
      cellClassName: "whitespace-nowrap",
      render: (f) => {
        const tier = docDriftConfidenceTier(f.confidence);
        return (
          <span className={`text-xs tabular-nums ${TIER_CLS[tier]}`}>
            {TIER_LABEL[tier]}{" "}
            <span className="font-mono text-[var(--color-text-tertiary)]">
              {f.confidence.toFixed(2)}
            </span>
          </span>
        );
      },
    },
  ];

  return (
    <ResponsiveTable
      columns={columns}
      rows={findings}
      rowKey={(f) => f.id}
      stacked="md"
      caption="Documentation assertions the repository no longer satisfies"
      {...(navigate
        ? {
            onRowClick: open,
            rowClassName: () => "cursor-pointer",
          }
        : {})}
      empty={
        <EmptyState
          icon={<FileText className="h-6 w-6" />}
          title="No findings in this slice"
          description="Widen the confidence floor or clear the reference filter to see the rest."
        />
      }
    />
  );
}
