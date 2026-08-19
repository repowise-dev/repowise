"use client";

import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "@repowise-dev/ui/shared";
import {
  ContractTypeBadge,
  RoleBadge,
} from "@repowise-dev/ui/workspace/contract-type-badge";
import type { WorkspaceContractEntry } from "@/lib/api/types";

type ContractRow = WorkspaceContractEntry & { _key: string };

/**
 * The contracts table.
 *
 * This is a client boundary for a mechanical reason rather than a stateful
 * one: `ResponsiveTable` takes its columns as `render` functions, and a
 * function cannot cross the server-to-client boundary. Keeping the column
 * definitions on this side lets the page itself stay a server component that
 * passes nothing but rows.
 */
const CONTRACT_COLUMNS: ResponsiveColumn<ContractRow>[] = [
  {
    key: "contract_id",
    header: "Contract",
    render: (c) => (
      <span className="font-mono text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
        {c.contract_id}
      </span>
    ),
  },
  {
    key: "contract_type",
    header: "Type",
    render: (c) => <ContractTypeBadge type={c.contract_type} />,
  },
  { key: "role", header: "Role", render: (c) => <RoleBadge role={c.role} /> },
  {
    key: "repo",
    header: "Repository",
    render: (c) => (
      <span className="text-xs font-medium text-[var(--color-text-primary)]">{c.repo}</span>
    ),
  },
  {
    key: "file_path",
    header: "File",
    priority: 2,
    render: (c) => (
      // No truncation: a path is exactly the string somebody is scanning for.
      <span className="font-mono text-xs text-[var(--color-text-tertiary)] [overflow-wrap:anywhere]">
        {c.file_path}
      </span>
    ),
  },
  {
    key: "confidence",
    header: "Confidence",
    align: "right",
    render: (c) => (
      <span className="text-xs tabular-nums text-[var(--color-text-tertiary)]">
        {Math.round(c.confidence * 100)}%
      </span>
    ),
  },
];

export function ContractsTable({ contracts }: { contracts: WorkspaceContractEntry[] }) {
  return (
    <ResponsiveTable
      columns={CONTRACT_COLUMNS}
      rows={contracts.map((c, i) => ({ ...c, _key: `${c.contract_id}|${i}` }))}
      rowKey={(c) => c._key}
      caption="All detected contracts"
      stacked="md"
      bare
    />
  );
}
