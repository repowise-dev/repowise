"use client";

/**
 * Dependency registry — the full `external_systems` table as a browsable
 * inventory: grouped by category, filterable by ecosystem / manifest /
 * dev-prod split, searchable by name. Presentational: data via props,
 * manifest links via render prop.
 */

import { useMemo, useState, type ReactNode } from "react";
import { Boxes, Cloud, Library, Search, Wrench, X } from "lucide-react";
import type {
  ExternalSystemEntry,
  ExternalSystemsRegistry,
} from "@repowise-dev/types/external-systems";
import { EmptyState } from "../shared/empty-state";
import { Badge } from "../ui/badge";
import { cn } from "../lib/cn";

const CATEGORY_ORDER = ["framework", "service", "tool", "library"] as const;

const CATEGORY_META: Record<string, { label: string; icon: typeof Boxes }> = {
  framework: { label: "Frameworks", icon: Boxes },
  service: { label: "Services", icon: Cloud },
  tool: { label: "Tools", icon: Wrench },
  library: { label: "Libraries", icon: Library },
};

export interface DependencyRegistryProps {
  data: ExternalSystemsRegistry;
  /** Renders a manifest path as a link (e.g. to the file page). Falls back
   *  to plain text. */
  renderManifestLink?: (declaredIn: string, children: ReactNode) => ReactNode;
}

type DepFilter = "all" | "prod" | "dev";

export function DependencyRegistry({
  data,
  renderManifestLink,
}: DependencyRegistryProps) {
  const [query, setQuery] = useState("");
  const [depFilter, setDepFilter] = useState<DepFilter>("all");
  const [ecosystem, setEcosystem] = useState<string | null>(null);
  const [manifest, setManifest] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return data.items.filter((e) => {
      if (depFilter === "prod" && e.is_dev_dep) return false;
      if (depFilter === "dev" && !e.is_dev_dep) return false;
      if (ecosystem && e.ecosystem !== ecosystem) return false;
      if (manifest && e.declared_in !== manifest) return false;
      if (q && !e.name.toLowerCase().includes(q) && !e.display_name.toLowerCase().includes(q))
        return false;
      return true;
    });
  }, [data.items, query, depFilter, ecosystem, manifest]);

  const grouped = useMemo(() => {
    const groups = new Map<string, ExternalSystemEntry[]>();
    for (const e of filtered) {
      const key = CATEGORY_META[e.category] ? e.category : "library";
      const list = groups.get(key);
      if (list) list.push(e);
      else groups.set(key, [e]);
    }
    return groups;
  }, [filtered]);

  if (data.total === 0) {
    return (
      <EmptyState
        title="No dependencies recorded"
        description="No manifest files (package.json, pyproject.toml, …) were parsed during indexing, or the repo declares no third-party dependencies."
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* Summary strip */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--color-text-secondary)]">
        <span>
          <span className="font-semibold text-[var(--color-text-primary)] tabular-nums">{data.total}</span>{" "}
          declared dependencies
        </span>
        <span className="tabular-nums">{data.prod_count} runtime</span>
        <span className="tabular-nums">{data.dev_count} dev-only</span>
        <span className="tabular-nums">
          {data.ecosystems.length} ecosystem{data.ecosystems.length === 1 ? "" : "s"}
        </span>
        <span className="tabular-nums">
          {data.manifests.length} manifest{data.manifests.length === 1 ? "" : "s"}
        </span>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 py-1.5">
          <Search className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search dependencies…"
            aria-label="Search dependencies"
            className="w-40 bg-transparent text-xs text-[var(--color-text-primary)] outline-none placeholder:text-[var(--color-text-tertiary)] sm:w-56"
          />
          {query && (
            <button onClick={() => setQuery("")} aria-label="Clear search">
              <X className="h-3 w-3 text-[var(--color-text-tertiary)]" />
            </button>
          )}
        </div>

        <div className="flex gap-0.5 rounded-md border border-[var(--color-border-default)] p-0.5">
          {(["all", "prod", "dev"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setDepFilter(f)}
              className={cn(
                "rounded px-2 py-1 text-xs font-medium transition-colors",
                depFilter === f
                  ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                  : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]",
              )}
              aria-pressed={depFilter === f}
            >
              {f === "all" ? "All" : f === "prod" ? "Runtime" : "Dev"}
            </button>
          ))}
        </div>

        {data.ecosystems.length > 1 && (
          <select
            value={ecosystem ?? ""}
            onChange={(e) => setEcosystem(e.target.value || null)}
            aria-label="Filter by ecosystem"
            className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 py-1.5 text-xs text-[var(--color-text-primary)] outline-none"
          >
            <option value="">All ecosystems</option>
            {data.ecosystems.map((eco) => (
              <option key={eco} value={eco}>
                {eco}
              </option>
            ))}
          </select>
        )}

        {data.manifests.length > 1 && (
          <select
            value={manifest ?? ""}
            onChange={(e) => setManifest(e.target.value || null)}
            aria-label="Filter by manifest"
            className="max-w-[260px] rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2 py-1.5 text-xs text-[var(--color-text-primary)] outline-none"
          >
            <option value="">All manifests</option>
            {data.manifests.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        )}
      </div>

      {filtered.length === 0 ? (
        <EmptyState
          title="No dependencies match"
          description="Adjust the search or filters to see more of the registry."
        />
      ) : (
        CATEGORY_ORDER.filter((c) => grouped.has(c)).map((category) => {
          const meta = CATEGORY_META[category]!;
          const Icon = meta.icon;
          const entries = grouped.get(category)!;
          return (
            <section key={category}>
              <h3 className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                <Icon className="h-3.5 w-3.5" />
                {meta.label}
                <span className="tabular-nums">({entries.length})</span>
              </h3>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {entries.map((e) => (
                  <div
                    key={`${e.name}:${e.declared_in}`}
                    className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p
                        className="min-w-0 truncate text-sm font-medium text-[var(--color-text-primary)]"
                        title={e.name}
                      >
                        {e.display_name || e.name}
                      </p>
                      <div className="flex shrink-0 items-center gap-1">
                        {e.is_dev_dep && (
                          <Badge variant="outline" className="text-[10px]">
                            dev
                          </Badge>
                        )}
                        <Badge variant="outline" className="text-[10px]">
                          {e.ecosystem}
                        </Badge>
                      </div>
                    </div>
                    <div className="mt-1.5 flex items-center justify-between gap-2 text-xs text-[var(--color-text-tertiary)]">
                      <span className="min-w-0 truncate font-mono" title={e.declared_in}>
                        {renderManifestLink
                          ? renderManifestLink(e.declared_in, e.declared_in)
                          : e.declared_in}
                      </span>
                      {e.version && (
                        <span className="shrink-0 font-mono tabular-nums">{e.version}</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          );
        })
      )}
    </div>
  );
}
