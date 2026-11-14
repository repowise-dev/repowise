"use client";

/**
 * The map's List view: every service as a sortable table row (name, group,
 * health, runtime links in and out, contracts), the text alternative to the
 * canvas. A row opens the service's details in the rail, as a click on its
 * card does.
 */

import { useMemo, useState } from "react";
import type { SystemGraph } from "@repowise-dev/types";
import { ResponsiveTable, type ResponsiveColumn } from "../../shared/responsive-table";
import type { SystemMapNavCopy } from "./nav-copy";
import {
  DEFAULT_SERVICE_SORT,
  nextSort,
  serviceRows,
  sortServiceRows,
  type ServiceRow,
  type ServiceSort,
  type ServiceSortKey,
} from "./service-rows";
import { SystemMapHealthMark } from "./system-map-health-mark";
import type { RepoHealth } from "./types";

export interface SystemMapListProps {
  graph: SystemGraph;
  healthByRepo?: ReadonlyMap<string, RepoHealth> | undefined;
  selectedId: string | null;
  onSelect: (serviceId: string) => void;
  copy: SystemMapNavCopy;
}

const FIGURE = "font-mono text-xs tabular-nums text-[var(--color-text-primary)]";

function columns(copy: SystemMapNavCopy, grouped: boolean): ResponsiveColumn<ServiceRow>[] {
  const cols: ResponsiveColumn<ServiceRow>[] = [
    {
      key: "name",
      header: copy.colService,
      sortable: true,
      priority: 1,
      render: (r) => (
        <span className="flex min-w-0 flex-col">
          <span className="truncate text-xs font-medium text-[var(--color-text-primary)]">{r.name}</span>
          {r.location !== r.name && (
            <span className="truncate font-mono text-[10px] text-[var(--color-text-secondary)]">{r.location}</span>
          )}
        </span>
      ),
    },
    {
      key: "group",
      header: copy.colGroup,
      sortable: true,
      priority: 2,
      render: (r) => <span className="text-xs text-[var(--color-text-secondary)]">{r.group ?? "–"}</span>,
    },
    {
      key: "health",
      header: copy.colHealth,
      sortable: true,
      priority: 1,
      render: (r) => <SystemMapHealthMark score={r.health} noScore={copy.noScore} />,
    },
    { key: "in", header: copy.colIn, sortable: true, align: "right", priority: 1, render: (r) => <span className={FIGURE}>{r.runtimeIn}</span> },
    { key: "out", header: copy.colOut, sortable: true, align: "right", priority: 1, render: (r) => <span className={FIGURE}>{r.runtimeOut}</span> },
    {
      key: "contracts",
      header: copy.colContracts,
      sortable: true,
      align: "right",
      priority: 3,
      render: (r) => <span className="text-xs tabular-nums text-[var(--color-text-secondary)]">{copy.contractsCell(r.provided, r.consumed)}</span>,
    },
  ];
  return grouped ? cols : cols.filter((c) => c.key !== "group");
}

export function SystemMapList({ graph, healthByRepo, selectedId, onSelect, copy }: SystemMapListProps) {
  const [sort, setSort] = useState<ServiceSort>(DEFAULT_SERVICE_SORT);
  const rows = useMemo(() => serviceRows(graph, healthByRepo), [graph, healthByRepo]);
  const sorted = useMemo(() => sortServiceRows(rows, sort), [rows, sort]);
  const grouped = rows.some((r) => r.group !== null);
  return (
    <ResponsiveTable
      columns={columns(copy, grouped)}
      rows={sorted}
      rowKey={(r) => r.id}
      onRowClick={(r) => onSelect(r.id)}
      selectedKey={selectedId}
      sortField={sort.key}
      sortOrder={sort.order}
      onSort={(key) => setSort((s) => nextSort(s, key as ServiceSortKey))}
      caption={copy.listCaption}
      stacked="sm"
      virtualize={{ maxHeight: 640 }}
      bare
    />
  );
}
