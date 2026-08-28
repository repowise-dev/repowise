"use client";

import React, { useCallback, useEffect, useMemo, useRef } from "react";
import Link from "next/link";
import { Package } from "lucide-react";
import useSWR from "swr";
import useSWRInfinite from "swr/infinite";
import {
  parseAsInteger,
  parseAsString,
  parseAsStringLiteral,
  useQueryState,
  useQueryStates,
} from "nuqs";
import {
  ExternalDependenciesTable,
  type ExternalDependencyTableState,
} from "@repowise-dev/ui/dependencies";
import { ApiError } from "@repowise-dev/ui/shared/api-error";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";
import type {
  ExternalSystemImportingFiles,
  ExternalSystemRelationshipGraph,
  ExternalSystemSummaryEntry,
  ExternalSystemsSummary,
} from "@repowise-dev/types/external-systems";
import { apiGet } from "@repowise-dev/api-client";
import "@/lib/api/client";
import {
  packageSummaryRequest,
  PACKAGE_SUMMARY_LIMIT,
} from "./package-graph";
import { queryFromTableState, tableStateFromQuery } from "./package-query-state";

const ROLES = ["all", "runtime", "dev-only", "mixed"] as const;
const USAGE_STATES = ["all", "observed", "linked-unobserved", "unlinked"] as const;
const SORTS = ["importers", "edges", "name", "declarations", "manifests", "versions"] as const;
const ORDERS = ["asc", "desc"] as const;
const SCOPES = ["primary", "all"] as const;
const FOCUSES = ["relationships"] as const;
const FILE_PAGE_LIMIT = 25;

interface ActiveRequest {
  identity: string;
  controller: AbortController;
}

export function DependenciesView({ repoId }: { repoId: string }) {
  const summaryAbortRef = useRef<AbortController | null>(null);
  const relationshipsAbortRef = useRef<ActiveRequest | null>(null);
  const filesAbortRef = useRef<ActiveRequest | null>(null);
  const [scope, setScope] = useQueryState(
    "scope",
    parseAsStringLiteral(SCOPES).withDefault("primary"),
  );
  const [selectedKey, setSelectedKey] = useQueryState("package", parseAsString);
  const [focus, setFocus] = useQueryState("focus", parseAsStringLiteral(FOCUSES));
  const [aggregateKey, setAggregateKey] = useQueryState("area", parseAsString);
  const [fileOffset, setFileOffset] = useQueryState("fileOffset", parseAsInteger.withDefault(0));
  const [queryState, setQueryState] = useQueryStates({
    q: parseAsString.withDefault(""),
    ecosystem: parseAsString.withDefault("all"),
    role: parseAsStringLiteral(ROLES).withDefault("all"),
    usage: parseAsStringLiteral(USAGE_STATES).withDefault("all"),
    category: parseAsString.withDefault("all"),
    sort: parseAsStringLiteral(SORTS).withDefault("importers"),
    order: parseAsStringLiteral(ORDERS).withDefault("desc"),
    page: parseAsInteger.withDefault(1),
  });

  const fetchSummary = useCallback((offset: number) => {
    summaryAbortRef.current?.abort();
    const controller = new AbortController();
    summaryAbortRef.current = controller;
    const request = packageSummaryRequest(repoId, scope);
    return apiGet<ExternalSystemsSummary>(
      request.path,
      { ...request.params, offset },
      { signal: controller.signal },
    );
  }, [repoId, scope]);

  const {
    data: summaryPages,
    error,
    isLoading,
    isValidating: summaryValidating,
    mutate,
    size,
    setSize,
  } = useSWRInfinite(
    (pageIndex, previous: ExternalSystemsSummary | null) => {
      if (previous && !previous.truncated) return null;
      return `external-systems-summary:${repoId}:${scope}:${pageIndex * PACKAGE_SUMMARY_LIMIT}`;
    },
    (key: string) => {
      const offset = Number(key.split(":").pop() ?? 0);
      return fetchSummary(offset);
    },
    { revalidateOnFocus: false, revalidateOnReconnect: false, persistSize: false },
  );

  const data = useMemo<ExternalSystemsSummary | undefined>(() => {
    if (!summaryPages?.length) return undefined;
    const first = summaryPages[0]!;
    const last = summaryPages[summaryPages.length - 1]!;
    const items = summaryPages.flatMap((page) => page.items);
    return {
      ...first,
      items,
      returned: items.length,
      offset: 0,
      truncated: last.truncated,
    };
  }, [summaryPages]);

  const selected = useMemo(
    () => data?.items.find((entry) => entry.package_key === selectedKey) ?? null,
    [data?.items, selectedKey],
  );

  // A shared URL can point at a package beyond the first bounded summary page.
  // Walk only the existing summary pages until it is found; relationship data
  // remains dormant until both package and focus have been restored.
  useEffect(() => {
    if (selectedKey && data && !selected && data.truncated && !summaryValidating) {
      void setSize(size + 1);
    }
  }, [data, selected, selectedKey, setSize, size, summaryValidating]);

  const relationshipRequestIdentity =
    selected && focus === "relationships" ? `${repoId}:${scope}:${selected.package_key}` : null;

  // Relationship reads stay dormant until the inspector's explicit action.
  const fetchRelationships = useCallback(() => {
    relationshipsAbortRef.current?.controller.abort();
    const controller = new AbortController();
    relationshipsAbortRef.current = { identity: relationshipRequestIdentity!, controller };
    return apiGet<ExternalSystemRelationshipGraph>(
      `/api/repos/${repoId}/external-systems/${encodeURIComponent(selected!.package_key)}/graph`,
      { scope, node_limit: 50, edge_limit: 200 },
      { signal: controller.signal },
    );
  }, [relationshipRequestIdentity, repoId, scope, selected]);
  const {
    data: relationships,
    error: relationshipsError,
    isLoading: relationshipsLoading,
    mutate: retryRelationships,
  } = useSWR(
    selected && focus === "relationships"
      ? `external-system-relationships:${repoId}:${scope}:${selected.package_key}`
      : null,
    fetchRelationships,
    { revalidateOnFocus: false, revalidateOnReconnect: false },
  );

  const fileRequestIdentity =
    relationshipRequestIdentity && aggregateKey
      ? `${relationshipRequestIdentity}:${aggregateKey}:${fileOffset}`
      : null;

  const fetchImportingFiles = useCallback(() => {
    filesAbortRef.current?.controller.abort();
    const controller = new AbortController();
    filesAbortRef.current = { identity: fileRequestIdentity!, controller };
    return apiGet<ExternalSystemImportingFiles>(
      `/api/repos/${repoId}/external-systems/${encodeURIComponent(selected!.package_key)}/graph/files`,
      {
        scope,
        aggregate_key: aggregateKey!,
        limit: FILE_PAGE_LIMIT,
        offset: fileOffset,
      },
      { signal: controller.signal },
    );
  }, [aggregateKey, fileOffset, fileRequestIdentity, repoId, scope, selected]);
  const {
    data: importingFiles,
    error: importingFilesError,
    isLoading: importingFilesLoading,
    mutate: retryImportingFiles,
  } = useSWR(
    selected && focus === "relationships" && aggregateKey
      ? `external-system-files:${repoId}:${scope}:${selected.package_key}:${aggregateKey}:${fileOffset}`
      : null,
    fetchImportingFiles,
    { revalidateOnFocus: false, revalidateOnReconnect: false },
  );

  useEffect(() => () => {
    const active = relationshipsAbortRef.current;
    if (active?.identity === relationshipRequestIdentity) {
      active.controller.abort();
      relationshipsAbortRef.current = null;
    }
  }, [relationshipRequestIdentity]);
  useEffect(() => () => {
    const active = filesAbortRef.current;
    if (active?.identity === fileRequestIdentity) {
      active.controller.abort();
      filesAbortRef.current = null;
    }
  }, [fileRequestIdentity]);
  useEffect(
    () => () => {
      summaryAbortRef.current?.abort();
      relationshipsAbortRef.current?.controller.abort();
      filesAbortRef.current?.controller.abort();
    },
    [],
  );

  const tableState: ExternalDependencyTableState = tableStateFromQuery(queryState);

  const handleTableStateChange = (next: ExternalDependencyTableState) => {
    void setQueryState(queryFromTableState(next));
  };

  return (
    <div className="max-w-[1600px] space-y-5 p-4 sm:p-6">
      <div className="flex flex-col gap-3 border-b border-[var(--color-border-default)] pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="mb-1 flex items-center gap-2 text-xl font-semibold text-[var(--color-text-primary)]">
            <Package className="h-5 w-5 text-[var(--color-accent-primary)]" />
            External dependencies
          </h1>
          <p className="max-w-3xl text-sm text-[var(--color-text-secondary)]">
            Declared third-party packages, joined to persisted import-graph evidence. Select a package to inspect declaration counts, versions, and importing files.
          </p>
        </div>
        {(scope === "all" || (data?.excluded_declarations ?? 0) > 0) ? <div className="shrink-0">
          <label className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
            <input
              type="checkbox"
              checked={scope === "all"}
              onChange={(event) => {
                void setScope(event.target.checked ? "all" : "primary");
                void setSelectedKey(null);
                void setFocus(null);
                void setAggregateKey(null);
                handleTableStateChange({ ...tableState, page: 1 });
              }}
              className="h-4 w-4 rounded border-[var(--color-border-default)] accent-[var(--color-accent-primary)]"
            />
            Include auxiliary declarations
          </label>
          <p className="mt-1 max-w-xs text-right text-2xs text-[var(--color-text-tertiary)]">
            {scope === "primary"
              ? `${data?.excluded_declarations ?? 0} declarations from auxiliary directories excluded`
              : "Showing primary and auxiliary directories present in this indexed checkout"}
          </p>
        </div> : null}
      </div>

      {error && !data ? (
        <ApiError
          title="Couldn't load external dependencies"
          message={toFriendlyMessage(error)}
          onRetry={() => void mutate()}
        />
      ) : isLoading || !data ? (
        <div className="space-y-4" aria-label="Loading external dependencies">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-9 w-full" />
          {Array.from({ length: 8 }).map((_, index) => (
            <Skeleton key={index} className="h-12 w-full" />
          ))}
        </div>
      ) : (
        <ExternalDependenciesTable
          data={data}
          state={tableState}
          onStateChange={handleTableStateChange}
          selected={selected}
          onSelectedChange={(entry) => {
            void setSelectedKey(entry?.package_key ?? null);
            void setFocus(null);
            void setAggregateKey(null);
            void setFileOffset(0);
          }}
          relationshipsOpen={focus === "relationships"}
          relationships={relationships}
          relationshipsLoading={relationshipsLoading}
          relationshipsError={
            relationshipsError
              ? `Package relationships couldn't be loaded: ${toFriendlyMessage(relationshipsError)}`
              : null
          }
          expandedAggregateKey={aggregateKey}
          importingFiles={importingFiles}
          importingFilesLoading={importingFilesLoading}
          importingFilesError={
            importingFilesError
              ? `Importing files couldn't be loaded: ${toFriendlyMessage(importingFilesError)}`
              : null
          }
          onShowRelationships={() => void setFocus("relationships")}
          onHideRelationships={() => {
            void setFocus(null);
            void setAggregateKey(null);
            void setFileOffset(0);
          }}
          onRetryRelationships={() => void retryRelationships()}
          onToggleAggregate={(key) => {
            void setAggregateKey(key);
            void setFileOffset(0);
          }}
          onFilesPageChange={(offset) => void setFileOffset(offset)}
          onRetryImportingFiles={() => void retryImportingFiles()}
          renderFileLink={(path, children) => (
            <Link
              href={fileEntityPath(`/repos/${repoId}`, path)}
              className="hover:text-[var(--color-accent-primary)] hover:underline"
            >
              {children}
            </Link>
          )}
          onLoadMore={data.truncated ? () => void setSize(size + 1) : undefined}
          loadingMore={summaryValidating && summaryPages !== undefined}
          loadMoreError={error ? toFriendlyMessage(error) : null}
          onRetryLoadMore={error ? () => void mutate() : undefined}
        />
      )}
    </div>
  );
}
