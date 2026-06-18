"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@repowise-dev/ui/ui/button";
import { getCoordinatorHealth, type CoordinatorHealth } from "@/lib/api/health";

interface Props {
  repoId: string;
  initial: CoordinatorHealth | null;
}

const STATUS_BADGE: Record<CoordinatorHealth["status"], string> = {
  ok: "bg-[var(--color-success)]/10 text-[var(--color-success)]",
  warning: "bg-[var(--color-warning)]/10 text-[var(--color-warning)]",
  critical: "bg-[var(--color-error)]/10 text-[var(--color-error)]",
};

function StatRow({ label, value, help }: { label: string; value: string; help?: string }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-[var(--color-border)] last:border-0">
      <span
        className={`text-xs text-[var(--color-text-secondary)] ${help ? "cursor-help underline decoration-dotted decoration-[var(--color-border-default)] underline-offset-2" : ""}`}
        title={help}
      >
        {label}
      </span>
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
          <StatRow
            label="Wiki Pages"
            value={`${fmt(data.sql_pages)} SQL / ${fmt(data.vector_page_count)} vectors`}
            help="Generated wiki pages in SQL vs the matching page vectors in the vector store."
          />
          <StatRow
            label="Page Drift"
            value={fmtPct(data.page_drift_pct)}
            help="How far the wiki-page count disagrees with the page-vector count. 0% means every page is embedded; high drift usually means an interrupted index — run a sync to reconcile."
          />
          <StatRow
            label="Decisions"
            value={`${fmt(data.sql_decisions)} SQL / ${fmt(data.vector_decision_count)} vectors`}
            help="Decision records in SQL vs the matching decision vectors. Decision vectors are counted separately from page vectors."
          />
          <StatRow
            label="Decision Drift"
            value={fmtPct(data.decision_drift_pct)}
            help="How far the decision-record count disagrees with the decision-vector count."
          />
          <StatRow label="Graph Nodes" value={fmt(data.graph_nodes)} />
          {data.detail && (
            <p className="text-xs text-[var(--color-text-secondary)] pt-1.5">{data.detail}</p>
          )}
        </>
      ) : (
        <p className="text-xs text-[var(--color-text-secondary)]">
          {error ?? "No data — click Refresh to load."}
        </p>
      )}
      {error && (
        <p className="text-xs text-[var(--color-error)]">{error}</p>
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
