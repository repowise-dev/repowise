"use client";

import { useState } from "react";
import { Link2, Filter } from "lucide-react";
import { useWorkspaceContracts } from "@/lib/hooks/use-workspace";
import { useWorkspace } from "@/lib/hooks/use-workspace";
import { ContractLinksTable } from "@repowise/ui/workspace/contract-links-table";
import { ContractTypeBadge, RoleBadge } from "@repowise/ui/workspace/contract-type-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@repowise/ui/ui/card";
import { StatCard } from "@repowise/ui/shared/stat-card";
import { Skeleton } from "@repowise/ui/ui/skeleton";

const TYPE_OPTIONS = [
  { value: "", label: "All Types" },
  { value: "http", label: "HTTP" },
  { value: "grpc", label: "gRPC" },
  { value: "topic", label: "Topic" },
];

const ROLE_OPTIONS = [
  { value: "", label: "All Roles" },
  { value: "provider", label: "Providers" },
  { value: "consumer", label: "Consumers" },
];

export default function ContractsPage() {
  const { workspace } = useWorkspace();
  const [contractType, setContractType] = useState("");
  const [repo, setRepo] = useState("");
  const [role, setRole] = useState("");

  const { data, isLoading } = useWorkspaceContracts({
    contract_type: contractType || undefined,
    repo: repo || undefined,
    role: role || undefined,
  });

  const repos = workspace?.repos ?? [];

  // Count unmatched contracts (providers with no link, consumers with no link)
  const linkedContractIds = new Set(
    (data?.links ?? []).map((l) => l.contract_id),
  );
  const unmatchedCount = (data?.contracts ?? []).filter(
    (c) => !linkedContractIds.has(c.contract_id),
  ).length;

  const selectClass =
    "rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-1.5 text-sm text-[var(--color-text-primary)] outline-none focus:border-[var(--color-accent-primary)]";

  return (
    <div className="p-5 sm:p-8 space-y-6 max-w-[1200px]">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2.5 mb-1">
          <Link2 className="h-6 w-6 text-[var(--color-accent-primary)]" />
          <h1 className="text-2xl font-semibold text-[var(--color-text-primary)]">
            Contracts
          </h1>
        </div>
        <p className="text-sm text-[var(--color-text-secondary)]">
          HTTP routes, gRPC services, and message topics detected across repositories.
        </p>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="Total Contracts"
          value={data?.total_contracts ?? "—"}
          icon={<Link2 className="h-4 w-4" />}
        />
        <StatCard
          label="Matched Links"
          value={data?.total_links ?? "—"}
          icon={<Link2 className="h-4 w-4 text-green-400" />}
        />
        <StatCard
          label="Unmatched"
          value={isLoading ? "—" : unmatchedCount}
          description="No matching provider or consumer"
          icon={<Link2 className="h-4 w-4 text-yellow-400" />}
        />
        <StatCard
          label="By Type"
          value={
            data?.by_type
              ? Object.values(data.by_type).reduce((a, b) => a + b, 0)
              : "—"
          }
          description={
            data?.by_type
              ? Object.entries(data.by_type)
                  .map(([k, v]) => `${v} ${k}`)
                  .join(", ")
              : undefined
          }
          icon={<Filter className="h-4 w-4 text-purple-400" />}
        />
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-[var(--color-text-tertiary)]" />
          <span className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
            Filters
          </span>
        </div>
        <select
          value={contractType}
          onChange={(e) => setContractType(e.target.value)}
          className={selectClass}
        >
          {TYPE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <select
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          className={selectClass}
        >
          <option value="">All Repos</option>
          {repos.map((r) => (
            <option key={r.alias} value={r.alias}>
              {r.alias}
            </option>
          ))}
        </select>
        <select
          value={role}
          onChange={(e) => setRole(e.target.value)}
          className={selectClass}
        >
          {ROLE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      {/* Contract Links */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">
            Matched Contract Links ({data?.total_links ?? 0})
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          {isLoading ? (
            <div className="space-y-3 py-4">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : (
            <ContractLinksTable links={data?.links ?? []} />
          )}
        </CardContent>
      </Card>

      {/* All Contracts */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">
            All Detected Contracts ({data?.total_contracts ?? 0})
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          {isLoading ? (
            <div className="space-y-3 py-4">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : (data?.contracts ?? []).length === 0 ? (
            <p className="text-sm text-[var(--color-text-tertiary)] py-4 text-center">
              No contracts detected.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--color-border-default)] text-left text-xs text-[var(--color-text-tertiary)]">
                    <th className="pb-2 pr-4 font-medium">Contract ID</th>
                    <th className="pb-2 pr-4 font-medium">Type</th>
                    <th className="pb-2 pr-4 font-medium">Role</th>
                    <th className="pb-2 pr-4 font-medium">Repo</th>
                    <th className="pb-2 pr-4 font-medium">File</th>
                    <th className="pb-2 font-medium w-20">Confidence</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--color-border-default)]">
                  {(data?.contracts ?? []).map((c, i) => (
                    <tr key={i} className="hover:bg-[var(--color-bg-elevated)] transition-colors">
                      <td className="py-2 pr-4">
                        <span className="text-xs font-mono text-[var(--color-text-secondary)] break-all">
                          {c.contract_id}
                        </span>
                      </td>
                      <td className="py-2 pr-4">
                        <ContractTypeBadge type={c.contract_type} />
                      </td>
                      <td className="py-2 pr-4">
                        <RoleBadge role={c.role} />
                      </td>
                      <td className="py-2 pr-4 text-xs font-medium text-[var(--color-text-primary)]">
                        {c.repo}
                      </td>
                      <td className="py-2 pr-4">
                        <span className="text-xs font-mono text-[var(--color-text-tertiary)] truncate block max-w-[200px]">
                          {c.file_path}
                        </span>
                      </td>
                      <td className="py-2 text-xs text-[var(--color-text-tertiary)] tabular-nums">
                        {Math.round(c.confidence * 100)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
