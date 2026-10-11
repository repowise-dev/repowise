"use client";

import { memo, useMemo, type MouseEvent } from "react";
import { ResponsiveTable, type ResponsiveColumn } from "@repowise-dev/ui/shared/responsive-table";
import { ContractTypeBadge, RoleBadge } from "@repowise-dev/ui/workspace/contract-type-badge";
import {
  ContractIdentity,
  FilePathText,
  distinctService,
} from "@repowise-dev/ui/workspace/contract-identity";
import { contractMetaString } from "@repowise-dev/ui/workspace/contract-facts";
import type { WorkspaceContractEntry } from "@/lib/api/types";
import { useOpenContract } from "./contract-drawer-host";
import { contractDetailHref } from "./contract-href";

type ContractRow = WorkspaceContractEntry & { _key: string };

/** How extraction found the contract, as the word a developer would recognise. */
function foundBy(c: WorkspaceContractEntry): string | null {
  return contractMetaString(c.meta, "framework") ?? contractMetaString(c.meta, "client");
}

const TYPE_COLUMN: ResponsiveColumn<ContractRow> = {
  key: "contract_type",
  header: "Type",
  priority: 3,
  render: (c) => <ContractTypeBadge type={c.contract_type} />,
};

const COLUMNS: ResponsiveColumn<ContractRow>[] = [
  {
    key: "contract_id",
    header: "Contract",
    cellClassName: "min-w-[180px] max-w-[340px]",
    // An anchor to the detail route, so middle-click and open-in-new-tab work.
    // An unmodified click is cancelled and bubbles to the row, which opens the
    // drawer instead of leaving the page.
    render: (c) => (
      <a
        href={contractDetailHref(c)}
        onClick={(e: MouseEvent<HTMLAnchorElement>) => {
          if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) {
            e.stopPropagation();
            return;
          }
          e.preventDefault();
        }}
        className="rounded-sm hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
      >
        <ContractIdentity contractId={c.contract_id} />
      </a>
    ),
  },
  { key: "role", header: "Role", render: (c) => <RoleBadge role={c.role} /> },
  {
    key: "repo",
    header: "Repository",
    priority: 2,
    render: (c) => {
      const service = distinctService(c.service, c.file_path);
      return (
        <span className="text-xs font-medium text-[var(--color-text-primary)]">
          {c.repo}
          {service ? (
            <span className="block font-mono text-[10px] font-normal text-[var(--color-text-tertiary)]">
              {service}
            </span>
          ) : null}
        </span>
      );
    },
  },
  {
    key: "file_path",
    header: "File",
    render: (c) => <FilePathText path={c.file_path} line={c.line} />,
  },
  {
    key: "found_by",
    header: "Found by",
    align: "right",
    priority: 3,
    render: (c) => {
      const via = foundBy(c);
      return (
        <span className="whitespace-nowrap font-mono text-[10px] text-[var(--color-text-tertiary)]">
          {via ? `${via} ` : ""}
          <span className="tabular-nums">{Math.round(c.confidence * 100)}%</span>
        </span>
      );
    },
  },
];

/**
 * The contracts table. A client leaf because its rows open the page's drawer;
 * everything around it stays server-rendered. Memoised so opening the drawer
 * or typing in the search box does not redraw the page of rows.
 */
export const ContractsTable = memo(function ContractsTable({
  contracts,
  showType,
}: {
  contracts: WorkspaceContractEntry[];
  /** False when a type tab is selected, since every row would repeat it. */
  showType: boolean;
}) {
  const open = useOpenContract();
  const columns = useMemo(
    () => (showType ? [COLUMNS[0]!, TYPE_COLUMN, ...COLUMNS.slice(1)] : COLUMNS),
    [showType],
  );
  const rows = useMemo(
    () =>
      contracts.map((c) => ({
        ...c,
        // `line` is part of the key because one file can call the same endpoint
        // from two places.
        _key: `${c.repo}|${c.file_path}|${c.contract_id}|${c.role}|${c.line ?? ""}`,
      })),
    [contracts],
  );

  return (
    <ResponsiveTable
      columns={columns}
      onRowClick={(c) =>
        open({ repo: c.repo, file_path: c.file_path, contract_id: c.contract_id }, c)
      }
      rows={rows}
      rowKey={(c) => c._key}
      caption="Detected contracts"
      stacked="md"
      bare
    />
  );
});
