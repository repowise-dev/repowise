"use client";

import { useMemo, useState, type ReactNode, type ComponentType } from "react";
import { RefreshCw, Trash2, AlertTriangle, Zap, MoreHorizontal } from "lucide-react";
import { Button } from "../ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "../ui/dialog";
import { formatNumber, formatCost, formatRelativeTime } from "../lib/format";

// Weighted average per-page token heuristics from cost_estimator.py _TOKEN_HEURISTICS
const AVG_INPUT_TOKENS_PER_PAGE = 3500;
const AVG_OUTPUT_TOKENS_PER_PAGE = 2200;

const COST_TABLE_EXACT: Record<string, [number, number]> = {
  "gpt-5.4": [0.0025, 0.015],
  "gpt-5.4-mini": [0.00075, 0.0045],
  "gpt-5.4-nano": [0.0002, 0.00125],
  "gpt-5.6-luna": [0.0002, 0.0012],
  "gemini-3.1-pro-preview": [0.002, 0.012],
  "gemini-3-flash-preview": [0.0005, 0.003],
  "gemini-3.1-flash-lite-preview": [0.00025, 0.0015],
  "claude-opus-4-6": [0.005, 0.025],
  "claude-sonnet-4-6": [0.003, 0.015],
  "claude-haiku-4-5": [0.001, 0.005],
  "deepseek-v4-flash": [0.00014, 0.00028],
  "deepseek-v4-pro": [0.00174, 0.00348],
};

const COST_TABLE_PREFIX: [string, [number, number]][] = [
  ["gpt-5.6-luna", [0.0002, 0.0012]],
  ["gpt-5.4-nano", [0.0002, 0.00125]],
  ["gpt-5.4-mini", [0.00075, 0.0045]],
  ["gpt-5.4", [0.0025, 0.015]],
  ["claude-opus", [0.005, 0.025]],
  ["claude-sonnet", [0.003, 0.015]],
  ["claude-haiku", [0.001, 0.005]],
  ["claude", [0.003, 0.015]],
  ["deepseek", [0.00014, 0.00028]],
  ["gemini", [0.00025, 0.0015]],
  ["llama", [0, 0]],
  ["mock", [0, 0]],
];

function lookupCost(modelName: string): [number, number] {
  const lower = modelName.toLowerCase();
  if (lower in COST_TABLE_EXACT) return COST_TABLE_EXACT[lower]!;
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

function estimateCost(pageCount: number, modelName: string) {
  const inputTokens = pageCount * AVG_INPUT_TOKENS_PER_PAGE;
  const outputTokens = pageCount * AVG_OUTPUT_TOKENS_PER_PAGE;
  const [inputRate, outputRate] = lookupCost(modelName);
  const cost = (inputTokens / 1000) * inputRate + (outputTokens / 1000) * outputRate;
  return { inputTokens, outputTokens, cost };
}

export type QuickActionKey = "sync" | "resync" | "dead-code";

export interface QuickActionDef {
  key: QuickActionKey;
  label: string;
  description: string;
  icon: ComponentType<{ className?: string }>;
  destructive: boolean;
  needsConfirm: boolean;
  confirmTitle: string;
  confirmDescription: string;
}

export const DEFAULT_QUICK_ACTIONS: QuickActionDef[] = [
  {
    key: "sync",
    label: "Sync",
    description: "Update everything â€” only affected pages regenerated",
    icon: Zap,
    destructive: false,
    needsConfirm: true,
    confirmTitle: "Sync Repository",
    confirmDescription:
      "Re-indexes the dependency graph, git metadata, dead code, and decisions. Only wiki pages affected by recent changes are regenerated (minimal LLM cost).",
  },
  {
    key: "resync",
    label: "Full Re-index",
    description: "Regenerate all docs from scratch",
    icon: RefreshCw,
    destructive: true,
    needsConfirm: true,
    confirmTitle: "Full Re-index",
    confirmDescription:
      "This regenerates every page from scratch. All existing pages will be overwritten.",
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

export interface QuickActionsProps {
  /** Action set to render. Defaults to {sync, resync, dead-code}. */
  actions?: QuickActionDef[];
  /** Bag of mutation callbacks; the shell calls the matching key. */
  onAction: (key: QuickActionKey) => Promise<void> | void;
  /** Disabled / loading flag for a specific action. Optional â€” if omitted the
   *  shell tracks its own optimistic loading state. */
  loadingKey?: QuickActionKey | null;
  /** ISO datetimes for the timestamp footer. */
  lastSyncAt?: string | null;
  lastResyncAt?: string | null;
  /** Inputs for the cost-estimate panel inside the confirm dialog. */
  pageCount?: number;
  modelName?: string;
  /** When provided, replaces the buttons with the given node â€” typically a
   *  rendered job-progress widget. Wrapper decides what to pass. */
  activeJobSlot?: ReactNode;
  /**
   * `row` (default) renders every action as an equal outline button above a
   * timestamp footer. `header` renders the first action as the one primary
   * button and folds the rest into an overflow menu, dropping the footer.
   *
   * The distinction is editorial, not cosmetic: sync is an everyday action,
   * while full re-index and dead-code scan are maintenance. Giving all three
   * the same weight made the destructive one as easy to hit as the safe one.
   */
  variant?: "row" | "header";
}

export function QuickActions({
  actions = DEFAULT_QUICK_ACTIONS,
  onAction,
  loadingKey,
  lastSyncAt,
  lastResyncAt,
  pageCount = 0,
  modelName = "",
  activeJobSlot,
  variant = "row",
}: QuickActionsProps) {
  const [internalLoading, setInternalLoading] = useState<QuickActionKey | null>(null);
  const [pendingAction, setPendingAction] = useState<QuickActionDef | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const loading = loadingKey !== undefined ? loadingKey : internalLoading;

  const estimate = useMemo(() => {
    if (!pendingAction || pendingAction.key === "dead-code" || pageCount <= 0) return null;
    const pages =
      pendingAction.key === "sync" ? Math.max(1, Math.ceil(pageCount * 0.1)) : pageCount;
    return estimateCost(pages, modelName);
  }, [pendingAction, pageCount, modelName]);

  async function execute(action: QuickActionDef) {
    if (loadingKey === undefined) setInternalLoading(action.key);
    setPendingAction(null);
    try {
      await onAction(action.key);
    } finally {
      if (loadingKey === undefined) setInternalLoading(null);
    }
  }

  function handleClick(action: QuickActionDef) {
    if (action.needsConfirm) {
      setPendingAction(action);
    } else {
      void execute(action);
    }
  }

  if (activeJobSlot) {
    return <div className="mb-2">{activeJobSlot}</div>;
  }

  // Shared by both variants: the confirm step (and its cost estimate) is the
  // safety rail, so it must not depend on which button shape triggered it.
  const confirmDialog = (
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
            <DialogDescription>{pendingAction.confirmDescription}</DialogDescription>
          </DialogHeader>

          {estimate && estimate.cost > 0 && (
            <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-inset)] p-3 space-y-2">
              <p className="text-xs font-medium text-[var(--color-text-secondary)] uppercase tracking-wider">
                Estimated Cost
              </p>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div>
                  <p className="text-sm font-semibold text-[var(--color-text-primary)] tabular-nums">
                    {formatNumber(
                      pendingAction.key === "sync"
                        ? Math.max(1, Math.ceil(pageCount * 0.1))
                        : pageCount,
                    )}
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
              onClick={() => execute(pendingAction)}
            >
              {pendingAction.label}
            </Button>
          </DialogFooter>
        </DialogContent>
      )}
    </Dialog>
  );

  if (variant === "header") {
    const [primary, ...rest] = actions;
    if (!primary) return null;
    const PrimaryIcon = primary.icon;
    return (
      <>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            className="h-8 gap-1.5 text-xs"
            disabled={loading !== null}
            onClick={() => handleClick(primary)}
          >
            <PrimaryIcon
              className={`h-3.5 w-3.5 ${loading === primary.key ? "motion-safe:animate-spin" : ""}`}
            />
            {primary.label}
          </Button>
          {rest.length > 0 && (
            // Controlled rather than uncontrolled: picking an item opens the
            // confirm dialog, and an uncontrolled popover stays mounted behind
            // the modal and reappears when the dialog is dismissed.
            <Popover open={menuOpen} onOpenChange={setMenuOpen}>
              <PopoverTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-8 w-8 p-0"
                  disabled={loading !== null}
                  aria-label="More actions"
                >
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-64 p-1">
                {rest.map((action) => {
                  const Icon = action.icon;
                  return (
                    <button
                      key={action.key}
                      type="button"
                      disabled={loading !== null}
                      onClick={() => {
                        setMenuOpen(false);
                        handleClick(action);
                      }}
                      className="flex w-full items-start gap-2.5 rounded-md px-2.5 py-2 text-left transition-colors hover:bg-[var(--color-bg-elevated)] disabled:opacity-50"
                    >
                      <Icon
                        className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${
                          action.destructive
                            ? "text-[var(--color-warning)]"
                            : "text-[var(--color-text-tertiary)]"
                        } ${loading === action.key ? "motion-safe:animate-spin" : ""}`}
                      />
                      <span className="min-w-0">
                        <span className="block text-xs font-medium text-[var(--color-text-primary)]">
                          {action.label}
                        </span>
                        <span className="block text-[11px] leading-snug text-[var(--color-text-tertiary)]">
                          {action.description}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </PopoverContent>
            </Popover>
          )}
        </div>
        {confirmDialog}
      </>
    );
  }

  return (
    <>
      <div className="space-y-1.5">
        <div className="flex flex-wrap gap-2">
          {actions.map((action) => {
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
                <Icon className={`h-3.5 w-3.5 ${isLoading ? "motion-safe:animate-spin" : ""}`} />
                {action.label}
              </Button>
            );
          })}
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-0.5">
          <span className="text-xs text-[var(--color-text-tertiary)]">
            Last synced{" "}
            <span className="text-[var(--color-text-secondary)]">
              {lastSyncAt ? formatRelativeTime(lastSyncAt) : "never"}
            </span>
          </span>
          <span className="text-xs text-[var(--color-text-tertiary)]">
            Last re-indexed{" "}
            <span className="text-[var(--color-text-secondary)]">
              {lastResyncAt ? formatRelativeTime(lastResyncAt) : "never"}
            </span>
          </span>
        </div>
      </div>
      {confirmDialog}
    </>
  );
}
