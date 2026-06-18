"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { RefreshCw } from "lucide-react";
import { syncWorkspace } from "@/lib/api/workspace";
import type { WorkspaceSyncResult } from "@/lib/api/types";

type SyncState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "ok"; results: WorkspaceSyncResult[] }
  | { kind: "error"; message: string };

interface SyncButtonProps {
  alias?: string;
  label?: string;
  variant?: "primary" | "ghost";
  fullResync?: boolean;
}

/**
 * Trigger /api/workspace/sync for the entire workspace (alias undefined)
 * or a single repo. Shows inline status; refreshes the route on success
 * so the new repo data shows up.
 */
export function SyncButton({
  alias,
  label,
  variant = "ghost",
  fullResync = false,
}: SyncButtonProps) {
  const [state, setState] = useState<SyncState>({ kind: "idle" });
  const [, startTransition] = useTransition();
  const router = useRouter();

  const handleClick = async () => {
    setState({ kind: "running" });
    try {
      const resp = await syncWorkspace({
        repoAlias: alias,
        fullResync,
      });
      setState({ kind: "ok", results: resp.results });
      startTransition(() => router.refresh());
    } catch (e) {
      setState({
        kind: "error",
        message: e instanceof Error ? e.message : "unknown error",
      });
    }
  };

  const buttonText =
    label ?? (alias ? "Sync this repo" : "Sync workspace");

  const baseClass =
    "inline-flex items-center gap-1.5 rounded-md text-xs font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
  const variantClass =
    variant === "primary"
      ? "bg-[var(--color-accent-primary)] text-[var(--color-bg-base)] hover:bg-[var(--color-accent-primary)]/90 px-3 py-1.5"
      : "border border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] px-2.5 py-1";

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={handleClick}
        disabled={state.kind === "running"}
        className={`${baseClass} ${variantClass}`}
        aria-label={buttonText}
      >
        <RefreshCw
          className={`h-3 w-3 ${state.kind === "running" ? "animate-spin" : ""}`}
        />
        {state.kind === "running" ? "Syncing…" : buttonText}
      </button>
      {state.kind === "ok" && (
        <span className="text-xs text-[var(--color-text-tertiary)]">
          {summarizeResults(state.results)}
        </span>
      )}
      {state.kind === "error" && (
        <span className="text-xs text-[var(--color-outdated)]">
          {state.message}
        </span>
      )}
    </div>
  );
}

function summarizeResults(results: WorkspaceSyncResult[]): string {
  if (results.length === 0) return "no repos";
  const accepted = results.filter((r) => r.status === "accepted").length;
  const skipped = results.filter((r) => r.status === "skipped").length;
  const errored = results.filter((r) => r.status === "error").length;
  const parts: string[] = [];
  if (accepted) parts.push(`${accepted} queued`);
  if (skipped) parts.push(`${skipped} skipped`);
  if (errored) parts.push(`${errored} error`);
  return parts.join(", ");
}
