"use client";

import { useState, type MouseEvent } from "react";
import {
  ResponsiveTable,
  type ResponsiveColumn,
} from "@repowise-dev/ui/shared";
import { fileEntityPath, symbolEntityPath } from "@repowise-dev/ui/shared/entity";
import {
  ContractTypeBadge,
  RoleBadge,
} from "@repowise-dev/ui/workspace/contract-type-badge";
import { ContractDrawer } from "@repowise-dev/ui/workspace/contract-drawer";
import { linksForContract } from "@repowise-dev/ui/workspace/contract-facts";
import type { WorkspaceContractEntry, WorkspaceContractLinkEntry } from "@/lib/api/types";
import { useWorkspaceBreakingChanges } from "@/lib/hooks/use-workspace";
import { contractDetailHref } from "./contract-href";

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
    // An anchor, so the detail route keeps its middle-click and its
    // open-in-new-tab. An unmodified click is cancelled and left to bubble to
    // the row, which opens the drawer instead of leaving the page.
    render: (c) => (
      <a
        href={contractDetailHref(c)}
        onClick={(e: MouseEvent<HTMLAnchorElement>) => {
          if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
            e.stopPropagation();
            return;
          }
          e.preventDefault();
        }}
        className="font-mono text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere] hover:underline"
      >
        {c.contract_id}
      </a>
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

export function ContractsTable({
  contracts,
  links = [],
  repoIds = {},
}: {
  contracts: WorkspaceContractEntry[];
  /** The page's matched links, narrowed to the open contract by the drawer. */
  links?: WorkspaceContractLinkEntry[];
  /** Repo alias to indexed repo id. A never-indexed repo has no entry. */
  repoIds?: Record<string, string>;
}) {
  const [selected, setSelected] = useState<WorkspaceContractEntry | null>(null);
  // The same report the section above renders, off the same SWR key, so
  // opening a row costs no second request.
  const { data: breaking } = useWorkspaceBreakingChanges();

  const prefixFor = (repo: string): string | null => {
    const id = repoIds[repo];
    return id ? `/repos/${id}` : null;
  };

  return (
    <>
      <ResponsiveTable
        columns={CONTRACT_COLUMNS}
        // The row is the verb. No "View" column and no icon cluster: there is
        // exactly one thing a contract row does, and `ResponsiveTable` already
        // carries the focus ring and the Enter/Space handling for both the table
        // and the stacked-card rendering.
        onRowClick={(c) => setSelected(c)}
        rows={contracts.map((c) => ({
          // The contract identity, not the array index: an index renumbers every
          // row whenever a filter changes. `line` is part of the key because one
          // file can call the same endpoint from two places, which is a duplicate
          // React key without it.
          ...c,
          _key: `${c.repo}|${c.file_path}|${c.contract_id}|${c.line ?? ""}`,
        }))}
        rowKey={(c) => c._key}
        caption="All detected contracts"
        stacked="md"
        bare
      />
      <ContractDrawer
        contract={selected}
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
        links={selected ? linksForContract(selected, links) : []}
        breakingChanges={
          selected
            ? (breaking?.changes ?? []).filter((c) => c.contract_id === selected.contract_id)
            : []
        }
        codeLinks={{
          symbolHref: (repo, symbolId) => {
            const prefix = prefixFor(repo);
            return prefix ? symbolEntityPath(prefix, symbolId) : null;
          },
          fileHref: (repo, file) => {
            const prefix = prefixFor(repo);
            return prefix ? fileEntityPath(prefix, file) : null;
          },
        }}
        {...(selected ? { fullPageHref: contractDetailHref(selected) } : {})}
      />
    </>
  );
}
