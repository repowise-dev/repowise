"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { RefreshCw, Trash2, AlertTriangle, Zap } from "lucide-react";
import { Button } from "@repowise-dev/ui/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@repowise-dev/ui/ui/dialog";
import { syncRepo, fullResyncRepo } from "@/lib/api/repos";
import { listJobs } from "@/lib/api/jobs";
import { analyzeDeadCode } from "@/lib/api/dead-code";
import { GenerationProgress } from "@/components/jobs/generation-progress";
import { formatNumber, formatCost, formatRelativeTime } from "@repowise-dev/ui/lib/format";

// Weighted average per-page token heuristics from cost_estimator.py _TOKEN_HEURISTICS
const AVG_INPUT_TOKENS_PER_PAGE = 3500;
const AVG_OUTPUT_TOKENS_PER_PAGE = 2200;

// Pricing per 1K tokens (input, output) â€” mirrors cost_estimator.py _COST_TABLE exactly
const COST_TABLE_EXACT: Record<string, [number, number]> = {
  "gpt-5.4": [0.0025, 0.015],
  "gpt-5.4-mini": [0.00075, 0.0045],
  "gpt-5.4-nano": [0.0002, 0.00125],
  "gemini-3.1-pro-preview": [0.002, 0.012],
  "gemini-3-flash-preview": [0.0005, 0.003],
  "gemini-3.1-flash-lite-preview": [0.00025, 0.0015],
  "claude-opus-4-6": [0.005, 0.025],
  "claude-sonnet-4-6": [0.003, 0.015],
  "claude-haiku-4-5": [0.001, 0.005],
};

// Prefix fallbacks â€” longest match wins (same as cost_estimator.py)
const COST_TABLE_PREFIX: [string, [number, number]][] = [
  ["gpt-5.4-nano", [0.0002, 0.00125]],
  ["gpt-5.4-mini", [0.00075, 0.0045]],
  ["gpt-5.4", [0.0025, 0.015]],
  ["claude-opus", [0.005, 0.025]],
  ["claude-sonnet", [0.003, 0.015]],
  ["claude-haiku", [0.001, 0.005]],
  ["claude", [0.003, 0.015]],
  ["gemini", [0.00025, 0.0015]],
  ["llama", [0, 0]],
  ["mock", [0, 0]],
];

function lookupCost(modelName: string): [number, number] {
  const lower = modelName.toLowerCase();
  if (lower in COST_TABLE_EXACT) return COST_TABLE_EXACT[lower];
  let bestLen = 0;
  let bestRates: [number, number] = [0, 0];
  for (const [prefix, rates] of COST_TABLE_PREFIX) {
    if (lower.startsWith(prefix) && prefix.length > bestLen) {
      bestLen = prefix.length;
      bestRates = rates;
    }
  }
  return bestRates;
}

function estimateCost(pageCount: number, modelName: string): { inputTokens: number; outputTokens: number; cost: number } {
  const inputTokens = pageCount * AVG_INPUT_TOKENS_PER_PAGE;
  const outputTokens = pageCount * AVG_OUTPUT_TOKENS_PER_PAGE;
  const [inputRate, outputRate] = lookupCost(modelName);
  // cost_estimator.py: (total_input / 1000) * input_rate + (total_output / 1000) * output_rate
  const cost = (inputTokens / 1000) * inputRate + (outputTokens / 1000) * outputRate;
  return { inputTokens, outputTokens, cost };
}

type ActionKey = "sync" | "resync" | "dead-code";

interface ActionDef {
  key: ActionKey;
  label: string;
  description: string;
  icon: typeof RefreshCw;
  destructive: boolean;
  needsConfirm: boolean;
  confirmTitle: string;
  confirmDescription: string;
}

const ACTIONS: ActionDef[] = [
  {
    key: "sync",
    label: "Sync",
    description: "Update everything â€” only affected pages regenerated",
    icon: Zap,
    destructive: false,
    needsConfirm: true,
    confirmTitle: "Sync Repository",
    confirmDescription: "Re-indexes the dependency graph, git metadata, dead code, and decisions. Only wiki pages affected by recent changes are regenerated (minimal LLM cost).",
  },
  {
    key: "resync",
    label: "Full Re-index",
    description: "Regenerate all docs from scratch",
    icon: RefreshCw,
    destructive: true,
    needsConfirm: true,
    confirmTitle: "Full Re-index",
    confirmDescription: "This regenerates every page from scratch. All existing pages will be overwritten.",
  },
  {
    key: "dead-code",
    label: "Dead Code Scan",
    description: "Analyze for unused exports and files",
    icon: Trash2,
    destructive: false,
    needsConfirm: false,
    confirmTitle: "",
    confirmDescription: "",
  },
];

interface QuickActionsProps {
  repoId: string;
  repoName?: string;
  pageCount?: number;
  modelName?: string;
  lastSyncAt?: string | null;
  lastResyncAt?: string | null;
}

export function QuickActions({ repoId, repoName, pageCount = 0, modelName = "", lastSyncAt, lastResyncAt }: QuickActionsProps) {
  const [loading, setLoading] = useState<ActionKey | null>(null);
  const [pendingAction, setPendingAction] = useState<ActionDef | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  // On mount, hydrate from any in-flight job so a page refresh shows live
  // progress instead of pretending nothing is happening. Without this, a
  // job stuck in 'pending' is invisible from the UI even though the
  // sync-blocked guard is still firing on the server.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [running, pending] = await Promise.all([
          listJobs({ repo_id: repoId, status: "running", limit: 1 }),
          listJobs({ repo_id: repoId, status: "pending", limit: 1 }),
        ]);
        if (cancelled) return;
        const inflight = running[0] ?? pending[0];
        if (inflight) setActiveJobId(inflight.id);
      } catch {
        // best-effort hydration â€” don't block the UI
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [repoId]);

  // Sync regenerates ~10-15% of pages (only affected by changes).
  // Full resync regenerates all pages.
  const estimate = pageCount > 0 && pendingAction?.key !== "dead-code"
    ? estimateCost(
        pendingAction?.key === "sync" ? Math.max(1, Math.ceil(pageCount * 0.1)) : pageCount,
        modelName
      )
    : null;

  async function executeAction(action: ActionDef) {
    setLoading(action.key);
    setPendingAction(null);
    try {
      if (action.key === "sync") {
        const job = await syncRepo(repoId);
        setActiveJobId(job.id);
        toast.info(`Sync started${repoName ? ` â€” ${repoName}` : ""}`);
      } else if (action.key === "resync") {
        const job = await fullResyncRepo(repoId);
        setActiveJobId(job.id);
        toast.info(`Full resync started${repoName ? ` â€” ${repoName}` : ""}`);
      } else if (action.key === "dead-code") {
        await analyzeDeadCode(repoId);
        toast.info("Dead code analysis started");
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Unknown error";
      // If the server already has an in-flight job, surface that one's
      // progress instead of just showing an opaque 409.
      if (/already in progress/i.test(msg)) {
        try {
          const [running, pending] = await Promise.all([
            listJobs({ repo_id: repoId, status: "running", limit: 1 }),
            listJobs({ repo_id: repoId, status: "pending", limit: 1 }),
          ]);
          const inflight = running[0] ?? pending[0];
          if (inflight) {
            setActiveJobId(inflight.id);
            toast.info("Showing progress for the in-flight job. Cancel it from the panel to start a new one.");
            return;
          }
        } catch {
          // fall through to error toast
        }
      }
      toast.error(`${action.label} failed`, { description: msg });
    } finally {
      setLoading(null);
    }
  }

  function handleClick(action: ActionDef) {
    if (action.needsConfirm) {
      setPendingAction(action);
    } else {
      executeAction(action);
    }
  }

  return (
    <>
      {/* Active job progress */}
      {activeJobId && (
        <div className="mb-2">
          <GenerationProgress
            jobId={activeJobId}
            repoName={repoName}
            onDone={() => setActiveJobId(null)}
          />
        </div>
      )}

      {/* Action buttons + timestamps */}
      {!activeJobId && (
        <div className="space-y-1.5">
          <div className="flex flex-wrap gap-2">
            {ACTIONS.map((action) => {
              const Icon = action.icon;
              const isLoading = loading === action.key;
              return (
                <Button
                  key={action.key}
                  variant="outline"
                  size="sm"
                  className="h-8 gap-1.5 text-xs"
                  disabled={loading !== null}
                  onClick={() => handleClick(action)}
                >
                  <Icon className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`} />
                  {action.label}
                </Button>
              );
            })}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-0.5">
            <span className="text-[11px] text-[var(--color-text-tertiary)]">
              Last synced{" "}
              <span className="text-[var(--color-text-secondary)]">
                {lastSyncAt ? formatRelativeTime(lastSyncAt) : "never"}
              </span>
            </span>
            <span className="text-[11px] text-[var(--color-text-tertiary)]">
              Last re-indexed{" "}
              <span className="text-[var(--color-text-secondary)]">
                {lastResyncAt ? formatRelativeTime(lastResyncAt) : "never"}
              </span>
            </span>
          </div>
        </div>
      )}

      {/* Confirmation dialog */}
      <Dialog open={pendingAction !== null} onOpenChange={(open) => !open && setPendingAction(null)}>
        {pendingAction && (
          <DialogContent className="sm:max-w-sm">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                {pendingAction.destructive && (
                  <AlertTriangle className="h-4 w-4 text-[var(--color-warning)]" />
                )}
                {pendingAction.confirmTitle}
              </DialogTitle>
              <DialogDescription>
                {pendingAction.confirmDescription}
              </DialogDescription>
            </DialogHeader>

            {/* Cost estimate */}
            {estimate && estimate.cost > 0 && (
              <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-inset)] p-3 space-y-2">
                <p className="text-xs font-medium text-[var(--color-text-secondary)] uppercase tracking-wider">
                  Estimated Cost
                </p>
                <div className="grid grid-cols-3 gap-2 text-center">
                  <div>
                    <p className="text-sm font-semibold text-[var(--color-text-primary)] tabular-nums">
                      {formatNumber(pendingAction.key === "sync" ? Math.max(1, Math.ceil(pageCount * 0.1)) : pageCount)}
                    </p>
                    <p className="text-[10px] text-[var(--color-text-tertiary)]">pages</p>
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-[var(--color-text-primary)] tabular-nums">
                      {formatNumber(estimate.inputTokens + estimate.outputTokens)}
                    </p>
                    <p className="text-[10px] text-[var(--color-text-tertiary)]">tokens</p>
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-[var(--color-accent-primary)] tabular-nums">
                      ~{formatCost(estimate.cost)}
                    </p>
                    <p className="text-[10px] text-[var(--color-text-tertiary)]">
                      {modelName || "est."}
                    </p>
                  </div>
                </div>
                {estimate.cost < 0.01 && (
                  <p className="text-[10px] text-[var(--color-text-tertiary)] text-center">
                    Free or near-free with this model
                  </p>
                )}
              </div>
            )}

            {estimate && estimate.cost === 0 && (
              <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-inset)] p-3">
                <p className="text-xs text-[var(--color-success)] text-center">
                  No API cost â€” running locally via Ollama
                </p>
              </div>
            )}

            <DialogFooter>
              <Button variant="ghost" size="sm" onClick={() => setPendingAction(null)}>
                Cancel
              </Button>
              <Button
                variant={pendingAction.destructive ? "destructive" : "default"}
                size="sm"
                onClick={() => executeAction(pendingAction)}
              >
                {pendingAction.label}
              </Button>
            </DialogFooter>
          </DialogContent>
        )}
      </Dialog>
    </>
  );
}
