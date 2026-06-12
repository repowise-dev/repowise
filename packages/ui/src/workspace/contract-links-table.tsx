"use client";

import { ContractTypeBadge } from "./contract-type-badge";
import { EmptyState } from "../shared/empty-state";
import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "../shared/responsive-table";
import type { WorkspaceContractLinkEntry } from "@repowise-dev/types/workspace";

interface ContractLinksTableProps {
  links: WorkspaceContractLinkEntry[];
}

const COLUMNS: ResponsiveColumn<WorkspaceContractLinkEntry>[] = [
  {
    key: "contract",
    header: "Contract",
    priority: 1,
    cellClassName: "min-w-[140px] max-w-[280px]",
    render: (link) => (
      <span
        className="text-xs font-mono text-[var(--color-text-secondary)] [overflow-wrap:anywhere]"
        title={link.contract_id}
      >
        {link.contract_id}
      </span>
    ),
  },
  {
    key: "type",
    header: "Type",
    priority: 2,
    render: (link) => <ContractTypeBadge type={link.contract_type} />,
  },
  {
    key: "provider",
    header: "Provider",
    priority: 1,
    render: (link) => (
      <div className="flex flex-col gap-0.5">
        <span className="text-xs font-medium text-[var(--color-text-primary)]">
          {link.provider_repo}
        </span>
        <span
          className="text-xs font-mono text-[var(--color-text-tertiary)] truncate min-w-[140px] max-w-[260px] block"
          title={link.provider_file}
        >
          {link.provider_file}
        </span>
      </div>
    ),
    mobileRender: (link) => link.provider_repo,
  },
  {
    key: "consumer",
    header: "Consumer",
    priority: 2,
    render: (link) => (
      <div className="flex flex-col gap-0.5">
        <span className="text-xs font-medium text-[var(--color-text-primary)]">
          {link.consumer_repo}
        </span>
        <span
          className="text-xs font-mono text-[var(--color-text-tertiary)] truncate min-w-[140px] max-w-[260px] block"
          title={link.consumer_file}
        >
          {link.consumer_file}
        </span>
      </div>
    ),
    mobileRender: (link) => link.consumer_repo,
  },
  {
    key: "confidence",
    header: "Confidence",
    mobileLabel: "Conf",
    priority: 3,
    headerClassName: "w-20",
    render: (link) => (
      <div className="flex items-center gap-1.5">
        <div className="h-1.5 w-12 rounded-full bg-[var(--color-bg-inset)] overflow-hidden">
          <div
            className="h-full rounded-full transition-all"
            style={{
              width: `${Math.round(link.confidence * 100)}%`,
              backgroundColor:
                link.confidence >= 0.8
                  ? "var(--color-confidence-fresh)"
                  : link.confidence >= 0.6
                  ? "var(--color-confidence-stale)"
                  : "var(--color-confidence-outdated)",
            }}
          />
        </div>
        <span className="text-xs text-[var(--color-text-tertiary)] tabular-nums">
          {Math.round(link.confidence * 100)}%
        </span>
      </div>
    ),
    mobileRender: (link) => `${Math.round(link.confidence * 100)}%`,
  },
];

export function ContractLinksTable({ links }: ContractLinksTableProps) {
  return (
    <ResponsiveTable
      columns={COLUMNS}
      rows={links}
      rowKey={(link) =>
        `${link.contract_id}|${link.provider_repo}|${link.provider_file}|${link.consumer_repo}|${link.consumer_file}`
      }
      bare
      empty={
        <EmptyState
          title="No matched contract links"
          description="No API contracts link providers and consumers across these repos yet."
        />
      }
    />
  );
}
