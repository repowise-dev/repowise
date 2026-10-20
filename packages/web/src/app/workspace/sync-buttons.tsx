"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { RefreshCw, Trash2 } from "lucide-react";
import { removeWorkspaceRepo, syncWorkspace } from "@/lib/api/workspace";
import type { WorkspaceSyncResult } from "@/lib/api/types";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";

/** The minimal translator shape `summarizeResults` needs; next-intl's `t` fits. */
type Translator = (key: string, values?: Record<string, string | number>) => string;

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
  const t = useTranslations("views.workspace");
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
        message: toFriendlyMessage(e),
      });
    }
  };

  const buttonText =
    label ?? (alias ? t("syncThisRepo") : t("syncWorkspace"));

  const baseClass =
    "inline-flex items-center gap-1.5 rounded-md text-xs font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
  const variantClass =
    variant === "primary"
      ? "bg-[var(--color-accent-primary)] text-[var(--color-text-on-accent)] hover:bg-[var(--color-accent-primary)]/90 px-3 py-1.5"
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
          className={`h-3 w-3 ${state.kind === "running" ? "motion-safe:animate-spin" : ""}`}
        />
        {state.kind === "running" ? t("syncing") : buttonText}
      </button>
      {state.kind === "ok" && (
        <span className="text-xs text-[var(--color-text-tertiary)]">
          {summarizeResults(t, state.results)}
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

function summarizeResults(t: Translator, results: WorkspaceSyncResult[]): string {
  if (results.length === 0) return t("syncNoRepos");
  const accepted = results.filter((r) => r.status === "accepted").length;
  const skipped = results.filter((r) => r.status === "skipped").length;
  const errored = results.filter((r) => r.status === "error").length;
  const parts: string[] = [];
  if (accepted) parts.push(t("syncQueued", { count: accepted }));
  if (skipped) parts.push(t("syncSkipped", { count: skipped }));
  if (errored) parts.push(t("syncErrors", { count: errored }));
  return parts.join(", ");
}

interface RemoveWorkspaceRepoButtonProps {
  alias: string;
  repoName?: string;
}

export function RemoveWorkspaceRepoButton({
  alias,
  repoName,
}: RemoveWorkspaceRepoButtonProps) {
  const t = useTranslations("views.workspace");
  const [removing, setRemoving] = useState(false);
  const [, startTransition] = useTransition();
  const router = useRouter();

  const handleRemove = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (
      typeof window !== "undefined" &&
      !window.confirm(t("removeConfirm", { name: repoName || alias }))
    ) {
      return;
    }
    setRemoving(true);
    try {
      await removeWorkspaceRepo(alias);
      startTransition(() => router.refresh());
    } catch (e) {
      if (typeof window !== "undefined") {
        window.alert(t("removeFailed", { message: toFriendlyMessage(e) }));
      }
    } finally {
      setRemoving(false);
    }
  };

  return (
    <button
      type="button"
      onClick={handleRemove}
      disabled={removing}
      className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border-default)] px-2.5 py-1 text-xs font-medium text-[var(--color-error)] hover:bg-[var(--color-bg-elevated)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      aria-label={t("removeAria", { alias })}
    >
      <Trash2 className="h-3 w-3" />
      {removing ? t("removing") : t("remove")}
    </button>
  );
}

