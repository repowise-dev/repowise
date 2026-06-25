"use client";

import { ContractTypeBadge } from "./contract-type-badge";
import { EmptyState } from "../shared/empty-state";
import { VirtualizedTable } from "../shared/virtualized-table";
import type { WorkspaceContractLinkEntry } from "@repowise-dev/types/workspace";

interface ContractLinksTableProps {
  links: WorkspaceContractLinkEntry[];
}

// Column-priority hide classes, mirroring the shared ResponsiveTable scale:
// priority 2 hides below md (768px), priority 3 hides below lg (1024px). The
// always-visible columns (priority 1) carry no hide class.
const HIDE_BELOW_MD = "max-md:hidden";
const HIDE_BELOW_LG = "max-lg:hidden";

export function ContractLinksTable({ links }: ContractLinksTableProps) {
  if (links.length === 0) {
    return (
      <EmptyState
        title="No matched contract links"
        description="No API contracts link providers and consumers across these repos yet."
      />
    );
  }

  const header = (
    <tr className="bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)] text-xs uppercase tracking-wider">
      <th className="px-3 py-2 text-left font-medium">Contract</th>
      <th className={`px-3 py-2 text-left font-medium ${HIDE_BELOW_MD}`}>Type</th>
      <th className="px-3 py-2 text-left font-medium">Provider</th>
      <th className={`px-3 py-2 text-left font-medium ${HIDE_BELOW_MD}`}>Consumer</th>
      <th className={`px-3 py-2 text-left font-medium w-20 ${HIDE_BELOW_LG}`}>Confidence</th>
    </tr>
  );

  const renderRow = (link: WorkspaceContractLinkEntry) => (
    <tr className="border-t border-[var(--color-border-default)] hover:bg-[var(--color-bg-elevated)]">
      <td className="px-3 py-2 text-left min-w-[140px] max-w-[280px]">
        <span
          className="text-xs font-mono text-[var(--color-text-secondary)] [overflow-wrap:anywhere]"
          title={link.contract_id}
        >
          {link.contract_id}
        </span>
      </td>
      <td className={`px-3 py-2 text-left ${HIDE_BELOW_MD}`}>
        <ContractTypeBadge type={link.contract_type} />
      </td>
      <td className="px-3 py-2 text-left">
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
      </td>
      <td className={`px-3 py-2 text-left ${HIDE_BELOW_MD}`}>
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
      </td>
      <td className={`px-3 py-2 text-left w-20 ${HIDE_BELOW_LG}`}>
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
      </td>
    </tr>
  );

  return (
    <VirtualizedTable<WorkspaceContractLinkEntry>
      rows={links}
      rowKey={(link) =>
        `${link.contract_id}|${link.provider_repo}|${link.provider_file}|${link.consumer_repo}|${link.consumer_file}`
      }
      header={header}
      renderRow={renderRow}
      aria-label="Cross-repo contract links"
    />
  );
}
