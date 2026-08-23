"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useTransition } from "react";
import { Filter } from "lucide-react";
import { contractTypeLabel } from "@repowise-dev/ui/workspace/contract-type-badge";
import { formatNumber } from "@repowise-dev/ui/lib/format";

/**
 * Every type the extractors can emit, in the order the select lists them.
 * Which of them a workspace actually holds is a separate question — see
 * `typeOptions`.
 */
const ALL_TYPES = ["http", "grpc", "socket", "topic", "data", "code"];

/**
 * The type options this workspace can act on.
 *
 * A control that cannot act must look like it, and three of the five options
 * this select used to offer could not return a row on any workspace here:
 * `grpc`, `socket` and `topic` are all zero, while `code` — the single largest
 * type — had no option at all. The distribution is already on the workspace
 * payload, so the control is built from it rather than from the vocabulary,
 * and the count goes on the label so a reader knows what a filter is worth
 * before spending a click on it.
 *
 * `byType` must be the workspace-wide breakdown, not the one on the contracts
 * response: that one is computed after the filters are applied, so selecting a
 * type would delete every other option and strand the reader inside it.
 *
 * With no breakdown available the full vocabulary comes back rather than an
 * empty select — an unfiltered list is a worse failure than an option that
 * returns nothing.
 */
function typeOptions(byType: Record<string, number> | null | undefined, selected: string) {
  const present = byType
    ? ALL_TYPES.filter((t) => (byType[t] ?? 0) > 0)
    : ALL_TYPES;
  // A type the vocabulary does not name still gets an option when the
  // workspace holds one, so a new extractor is filterable before this list is.
  const extra = byType
    ? Object.keys(byType).filter((t) => !ALL_TYPES.includes(t) && byType[t]! > 0)
    : [];
  const values = [...present, ...extra];
  // A type someone linked to directly stays selectable even when the workspace
  // holds none of it, or the select would render blank against its own URL.
  if (selected && !values.includes(selected)) values.push(selected);
  return [
    { value: "", label: "All types" },
    ...values.map((value) => ({
      value,
      label: byType?.[value]
        ? `${contractTypeLabel(value)} ${formatNumber(byType[value]!)}`
        : contractTypeLabel(value),
    })),
  ];
}

const ROLE_OPTIONS = [
  { value: "", label: "All roles" },
  { value: "provider", label: "Providers" },
  { value: "consumer", label: "Consumers" },
];

const SELECT_CLASS =
  "rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-1.5 text-sm text-[var(--color-text-primary)] outline-none focus:border-[var(--color-border-active)] disabled:opacity-60";

/**
 * The contracts table's filters.
 *
 * The only stateful thing on an otherwise server-rendered page, and it keeps
 * its state in the URL rather than in React: the page re-renders on the server
 * with the new filter, so a filtered view is linkable and the table's counts
 * come from the same request that drew the rows.
 */
export function ContractFilters({
  repos,
  byType,
}: {
  repos: string[];
  /** Workspace-wide contracts per type, from `contract_summary`. */
  byType?: Record<string, number> | null;
}) {
  const router = useRouter();
  const params = useSearchParams();
  const [pending, startTransition] = useTransition();
  const types = typeOptions(byType, params.get("type") ?? "");

  const set = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(params.toString());
      if (value) next.set(key, value);
      else next.delete(key);
      startTransition(() => {
        router.push(next.size > 0 ? `?${next.toString()}` : "/workspace/contracts");
      });
    },
    [params, router],
  );

  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
        <Filter className="h-3.5 w-3.5" aria-hidden />
        Filter
      </span>

      <label className="sr-only" htmlFor="contract-type">
        Contract type
      </label>
      <select
        id="contract-type"
        className={SELECT_CLASS}
        disabled={pending}
        value={params.get("type") ?? ""}
        onChange={(e) => set("type", e.target.value)}
      >
        {types.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>

      <label className="sr-only" htmlFor="contract-repo">
        Repository
      </label>
      <select
        id="contract-repo"
        className={SELECT_CLASS}
        disabled={pending}
        value={params.get("repo") ?? ""}
        onChange={(e) => set("repo", e.target.value)}
      >
        <option value="">All repositories</option>
        {repos.map((r) => (
          <option key={r} value={r}>
            {r}
          </option>
        ))}
      </select>

      <label className="sr-only" htmlFor="contract-role">
        Role
      </label>
      <select
        id="contract-role"
        className={SELECT_CLASS}
        disabled={pending}
        value={params.get("role") ?? ""}
        onChange={(e) => set("role", e.target.value)}
      >
        {ROLE_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}
