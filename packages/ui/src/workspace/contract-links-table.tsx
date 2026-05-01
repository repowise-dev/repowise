"use client";

import { ContractTypeBadge } from "./contract-type-badge";
import type { WorkspaceContractLinkEntry } from "@repowise/types/workspace";

interface ContractLinksTableProps {
  links: WorkspaceContractLinkEntry[];
}

export function ContractLinksTable({ links }: ContractLinksTableProps) {
  if (links.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-tertiary)] py-4 text-center">
        No matched contract links.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--color-border-default)] text-left text-xs text-[var(--color-text-tertiary)]">
            <th className="pb-2 pr-4 font-medium">Contract</th>
            <th className="pb-2 pr-4 font-medium">Type</th>
            <th className="pb-2 pr-4 font-medium">Provider</th>
            <th className="pb-2 pr-4 font-medium">Consumer</th>
            <th className="pb-2 font-medium w-20">Confidence</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--color-border-default)]">
          {links.map((link, i) => (
            <tr key={i} className="hover:bg-[var(--color-bg-elevated)] transition-colors">
              <td className="py-2 pr-4 min-w-[140px] max-w-[280px]">
                <span className="text-xs font-mono text-[var(--color-text-secondary)] [overflow-wrap:anywhere]" title={link.contract_id}>
                  {link.contract_id}
                </span>
              </td>
              <td className="py-2 pr-4">
                <ContractTypeBadge type={link.contract_type} />
              </td>
              <td className="py-2 pr-4">
                <div className="flex flex-col gap-0.5">
                  <span className="text-xs font-medium text-[var(--color-text-primary)]">
                    {link.provider_repo}
                  </span>
                  <span className="text-xs font-mono text-[var(--color-text-tertiary)] truncate min-w-[140px] max-w-[260px] block" title={link.provider_file}>
                    {link.provider_file}
                  </span>
                </div>
              </td>
              <td className="py-2 pr-4">
                <div className="flex flex-col gap-0.5">
                  <span className="text-xs font-medium text-[var(--color-text-primary)]">
                    {link.consumer_repo}
                  </span>
                  <span className="text-xs font-mono text-[var(--color-text-tertiary)] truncate min-w-[140px] max-w-[260px] block" title={link.consumer_file}>
                    {link.consumer_file}
                  </span>
                </div>
              </td>
              <td className="py-2">
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
          ))}
        </tbody>
      </table>
    </div>
  );
}
