"use client";

import * as React from "react";
import { Scale } from "lucide-react";
import { Badge } from "../ui/badge";
import { EmptyState } from "../shared/empty-state";
import { ResponsiveTable, type ResponsiveColumn } from "../shared/responsive-table";
import { stripMarkdown } from "../lib/format";
import type { GoverningDecisionRef } from "@repowise-dev/types/files";

const STATUS_VARIANT: Record<string, "default" | "fresh" | "stale" | "outdated" | "outline" | "accent"> = {
  active: "fresh",
  proposed: "accent",
  deprecated: "outdated",
  superseded: "outline",
};

export interface FileDecisionsTabProps {
  decisions: GoverningDecisionRef[] | undefined | null;
  linkPrefix: string;
  LinkComponent?:
    | React.ElementType<{
        href: string;
        className?: string;
        children: React.ReactNode;
      }>
    | undefined;
}

export function FileDecisionsTab({
  decisions,
  linkPrefix,
  LinkComponent = "a",
}: FileDecisionsTabProps) {
  const items = decisions ?? [];
  const Link = LinkComponent;

  if (items.length === 0) {
    return (
      <EmptyState
        icon={<Scale className="h-8 w-8" />}
        title="No governing decisions"
        description="This file is not directly linked to any architectural governing decisions."
      />
    );
  }

  const columns: ResponsiveColumn<GoverningDecisionRef>[] = [
    {
      key: "title",
      header: "Title",
      priority: 1,
      cellClassName: "min-w-[200px]",
      render: (d) => (
        <Link
          href={`${linkPrefix}/decisions/${d.id}`}
          className="font-medium text-[var(--color-text-primary)] hover:text-[var(--color-accent-primary)] hover:underline block truncate"
          title={stripMarkdown(d.title)}
        >
          {stripMarkdown(d.title)}
        </Link>
      ),
    },
    {
      key: "status",
      header: "Status",
      priority: 1,
      render: (d) => (
        <Badge variant={STATUS_VARIANT[d.status] ?? "outline"}>{d.status}</Badge>
      ),
    },
    {
      key: "action",
      header: "",
      priority: 2,
      align: "right",
      render: (d) => (
        <Link
          href={`${linkPrefix}/decisions/${d.id}`}
          className="text-xs text-[var(--color-accent-primary)] hover:underline font-medium"
        >
          View decision →
        </Link>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <ResponsiveTable
        columns={columns}
        rows={items}
        rowKey={(d) => d.id}
      />
    </div>
  );
}
