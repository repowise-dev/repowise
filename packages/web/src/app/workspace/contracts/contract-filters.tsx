"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useTransition } from "react";
import { Filter } from "lucide-react";

const TYPE_OPTIONS = [
  { value: "", label: "All types" },
  { value: "http", label: "HTTP" },
  { value: "grpc", label: "gRPC" },
  { value: "socket", label: "Socket" },
  { value: "topic", label: "Topic" },
  { value: "data", label: "Table" },
];

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
export function ContractFilters({ repos }: { repos: string[] }) {
  const router = useRouter();
  const params = useSearchParams();
  const [pending, startTransition] = useTransition();

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
        {TYPE_OPTIONS.map((o) => (
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
