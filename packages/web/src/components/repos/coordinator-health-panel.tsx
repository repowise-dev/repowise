"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getCoordinatorHealth, type CoordinatorHealth } from "@/lib/api/health";

interface Props {
  repoId: string;
  initial: CoordinatorHealth | null;
}

const STATUS_BADGE: Record<CoordinatorHealth["status"], string> = {
  ok: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
  warning: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
  critical: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
};

function StatRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-[var(--color-border)] last:border-0">
      <span className="text-xs text-[var(--color-text-secondary)]">{label}</span>
      <span className="text-xs font-medium text-[var(--color-text-primary)]">{value}</span>
    </div>
  );
}

export function CoordinatorHealthPanel({ repoId, initial }: Props) {
  const [data, setData] = useState<CoordinatorHealth | null>(initial);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const result = await getCoordinatorHealth(repoId);
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch health");
    } finally {
      setLoading(false);
    }
  }

  const fmt = (v: number | null) => (v === null ? "—" : String(v));
  const fmtPct = (v: number | null) => (v === null ? "—" : `${v.toFixed(1)}%`);

  return (
    <div className="space-y-3">
      {data ? (
        <>
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs text-[var(--color-text-secondary)]">Status</span>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_BADGE[data.status]}`}
            >
              {data.status}
            </span>
          </div>
          <StatRow label="SQL Pages" value={fmt(data.sql_pages)} />
          <StatRow label="Vector Count" value={fmt(data.vector_count)} />
          <StatRow label="Graph Nodes" value={fmt(data.graph_nodes)} />
          <StatRow label="Drift" value={fmtPct(data.drift_pct)} />
        </>
      ) : (
        <p className="text-xs text-[var(--color-text-secondary)]">
          {error ?? "No data — click Refresh to load."}
        </p>
      )}
      {error && (
        <p className="text-xs text-red-500">{error}</p>
      )}
      <Button
        variant="outline"
        size="sm"
        className="w-full mt-2"
        onClick={refresh}
        disabled={loading}
      >
        <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? "animate-spin" : ""}`} />
        {loading ? "Checking…" : "Refresh"}
      </Button>
    </div>
  );
}
