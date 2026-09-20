"use client";

/**
 * The drill-down: one row per drifted assertion.
 *
 * Column order is the reading order of the sentence the row makes: *this
 * document*, at *this line*, claims *this thing* exists, and here is why we
 * think it does not. The document leads because it is the file the reader
 * opens; putting the target first would read as a list of broken code files,
 * which is the opposite of what a finding says.
 *
 * A row opens the detail panel rather than navigating. The evidence that makes
 * a finding actionable does not fit in a row, and the document's own file page
 * cannot show it either.
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
import { AiPromptButton } from "../health/ai-prompt-button";

export interface DriftFindingsTableProps {
  findings: DocDriftFinding[];
  /** Open one finding's detail. The row is clickable when given. */
  onSelect: (finding: DocDriftFinding) => void;
  /** Hand one finding to an agent, from the row itself. */
  onPrompt: (finding: DocDriftFinding) => void;
  /** Which row the open panel is describing. */
  selectedId?: string | null | undefined;
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
  onSelect,
  onPrompt,
  selectedId,
}: DriftFindingsTableProps) {
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
    {
      key: "actions",
      header: "",
      align: "right",
      priority: 1,
      cellClassName: "whitespace-nowrap",
      // The button stops its own propagation, so it is safe inside a row that
      // also opens the panel.
      render: (f) => (
        <AiPromptButton
          variant="icon"
          onClick={() => onPrompt(f)}
          label={`Fix ${f.file_path}:${f.line_number} with an agent`}
        />
      ),
    },
  ];

  return (
    <ResponsiveTable
      columns={columns}
      rows={findings}
      rowKey={(f) => f.id}
      stacked="md"
      caption="Documentation assertions the repository no longer satisfies"
      onRowClick={onSelect}
      rowClassName={() => "cursor-pointer"}
      selectedKey={selectedId ?? null}
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
