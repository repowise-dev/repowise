"use client";

import { memo, useMemo } from "react";
import { ContractTypeBadge } from "./contract-type-badge";
import { ContractIdentity, FilePathText, distinctService } from "./contract-identity";
import { LINK_CONFIDENCE_NOTE } from "./contract-facts";
import { EmptyState } from "../shared/empty-state";
import { InfoTip } from "../shared/info-tip";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import type { WorkspaceContractLinkEntry } from "@repowise-dev/types/workspace";

interface ContractLinksTableProps {
  links: WorkspaceContractLinkEntry[];
  /** Open one link. The row is the verb; without this the rows are plain. */
  onSelect?: ((link: WorkspaceContractLinkEntry) => void) | undefined;
}

function Side({
  repo,
  service,
  file,
}: {
  repo: string;
  service: string | null;
  file: string;
}) {
  const shownService = distinctService(service, file);
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="text-xs font-medium text-[var(--color-text-primary)]">
        {repo}
        {shownService ? (
          <span className="font-mono font-normal text-[var(--color-text-tertiary)]">
            {" "}
            / {shownService}
          </span>
        ) : null}
      </span>
      <FilePathText path={file} />
    </div>
  );
}

const COLUMNS: ResponsiveColumn<WorkspaceContractLinkEntry>[] = [
  {
    key: "contract",
    header: "Contract",
    cellClassName: "min-w-[180px] max-w-[320px]",
    render: (l) => <ContractIdentity contractId={l.contract_id} />,
  },
  {
    key: "type",
    header: "Type",
    priority: 3,
    render: (l) => <ContractTypeBadge type={l.contract_type} />,
  },
  {
    key: "provider",
    header: "Provider",
    render: (l) => <Side repo={l.provider_repo} service={l.provider_service} file={l.provider_file} />,
  },
  {
    key: "consumer",
    header: "Consumer",
    priority: 2,
    render: (l) => <Side repo={l.consumer_repo} service={l.consumer_service} file={l.consumer_file} />,
  },
  {
    key: "confidence",
    // One explanation in the header instead of a bar per row: the figure is the
    // weaker side's extraction confidence, so it repeats in long runs.
    header: (
      <span className="inline-flex items-center gap-1">
        Confidence
        <InfoTip content={LINK_CONFIDENCE_NOTE} label="What link confidence means" />
      </span>
    ),
    mobileLabel: "Confidence",
    align: "right",
    priority: 3,
    render: (l) => (
      <span className="font-mono text-xs tabular-nums text-[var(--color-text-tertiary)]">
        {Math.round(l.confidence * 100)}%
      </span>
    ),
  },
];

function linkKey(l: WorkspaceContractLinkEntry): string {
  return `${l.contract_id}|${l.consumer_contract_id ?? ""}|${l.provider_repo}|${l.provider_file}|${l.consumer_repo}|${l.consumer_file}`;
}

/** Memoised so opening the drawer above it does not redraw every row. */
export const ContractLinksTable = memo(function ContractLinksTable({
  links,
  onSelect,
}: ContractLinksTableProps) {
  // Strongest first, then by contract: the extraction order put a run of the
  // weakest links on top, which read as if every link were weak.
  const sorted = useMemo(
    () =>
      links
        .slice()
        .sort(
          (a, b) => b.confidence - a.confidence || a.contract_id.localeCompare(b.contract_id),
        ),
    [links],
  );

  if (links.length === 0) {
    return (
      <EmptyState
        title="No matched contract links"
        description="No API contracts link providers and consumers across these repos yet."
      />
    );
  }

  return (

    <ResponsiveTable<WorkspaceContractLinkEntry>
      columns={COLUMNS}
      rows={sorted}
      rowKey={linkKey}
      onRowClick={onSelect}
      virtualize={{ estimateRowHeight: 56, estimateCardHeight: 120, maxHeight: 560 }}
      caption="Cross-repo contract links"
      stacked="md"
      bare
    />
  );
});
