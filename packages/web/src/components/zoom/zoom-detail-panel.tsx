"use client";

/**
 * Detail panel for the selected zoom node. A self-contained inspector (metadata,
 * rolled-up metrics, summary) plus a "zoom here" action that flies the camera
 * into the node. Kept lightweight and decoupled from the architecture-view
 * selection store so the zoom canvas owns its own selection lifecycle.
 */

import Link from "next/link";
import { FileCode, ScanSearch, X } from "lucide-react";
import type { ZoomNode } from "@repowise-dev/ui/zoom";
import { scoreTextColor } from "@repowise-dev/ui/health";

interface ZoomDetailPanelProps {
  node: ZoomNode;
  repoId: string;
  onClose: () => void;
  onZoom: (id: string) => void;
}

/** Route to a file's own page. Segments are encoded but the slashes are kept so
 *  the `/files/[...path]` catch-all receives the real path. */
function fileHref(repoId: string, path: string): string {
  const encoded = path.split("/").map(encodeURIComponent).join("/");
  return `/repos/${repoId}/files/${encoded}`;
}

const KIND_LABEL: Record<ZoomNode["kind"], string> = {
  system: "System",
  layer: "Layer",
  group: "Group",
  folder: "Folder",
  file: "File",
};

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-canvas)] px-2.5 py-1.5">
      <div className={`text-sm font-semibold ${tone ?? "text-[var(--color-text-primary)]"}`}>
        {value}
      </div>
      <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">{label}</div>
    </div>
  );
}

export function ZoomDetailPanel({ node, repoId, onClose, onZoom }: ZoomDetailPanelProps) {
  const m = node.metrics;
  const isFile = node.kind === "file";
  return (
    <aside className="absolute right-3 top-3 z-20 flex max-h-[calc(100%-1.5rem)] w-72 flex-col overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] shadow-xl">
      <header className="flex items-start justify-between gap-2 border-b border-[var(--color-border-default)] px-3 py-2.5">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="rounded bg-[var(--color-bg-canvas)] px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[var(--color-text-tertiary)]">
              {KIND_LABEL[node.kind]}
            </span>
            {node.is_entry_point && (
              <span className="text-[10px] font-medium text-[var(--color-success)]">entry</span>
            )}
            {node.on_flow && !node.is_entry_point && (
              <span className="text-[10px] font-medium text-[var(--color-accent-secondary)]">on flow</span>
            )}
          </div>
          <h2 className="mt-1 truncate text-sm font-semibold text-[var(--color-text-primary)]" title={node.name}>
            {node.name}
          </h2>
          {node.path && node.path !== node.name && (
            <p className="truncate text-xs text-[var(--color-text-tertiary)]" title={node.path}>
              {node.path}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close details"
          className="shrink-0 rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="flex-1 overflow-auto px-3 py-3">
        {node.summary && (
          <p className="mb-3 text-xs leading-relaxed text-[var(--color-text-secondary)]">{node.summary}</p>
        )}
        <div className="grid grid-cols-2 gap-2">
          {node.health_score !== null && (
            <div className="rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-canvas)] px-2.5 py-1.5">
              <div className={`text-sm font-semibold ${scoreTextColor(node.health_score)}`}>
                {node.health_score.toFixed(1)}
              </div>
              <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
                {isFile ? "health" : "health (avg)"}
              </div>
            </div>
          )}
          {!isFile && <Stat label="files" value={m.file_count} />}
          {m.hotspot_count > 0 && (
            <Stat label="hotspots" value={m.hotspot_count} tone="text-[var(--color-risk-high)]" />
          )}
          {m.entry_point_count > 0 && (
            <Stat label="entry points" value={m.entry_point_count} tone="text-[var(--color-success)]" />
          )}
          {m.on_flow_count > 0 && <Stat label="on flow" value={m.on_flow_count} />}
          {m.dead_count > 0 && <Stat label="dead" value={m.dead_count} />}
        </div>
        {node.language && (
          <div className="mt-3 text-xs text-[var(--color-text-tertiary)]">
            Language: <span className="text-[var(--color-text-secondary)]">{node.language}</span>
          </div>
        )}
      </div>

      {(node.children.length > 0 || isFile) && (
        <footer className="flex flex-col gap-2 border-t border-[var(--color-border-default)] p-2">
          {node.children.length > 0 && (
            <button
              type="button"
              onClick={() => onZoom(node.id)}
              className="flex w-full items-center justify-center gap-1.5 rounded-md bg-[var(--color-accent-primary)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-on-accent)] hover:opacity-90"
            >
              <ScanSearch className="h-3.5 w-3.5" />
              Zoom in
            </button>
          )}
          {isFile && node.path && (
            <Link
              href={fileHref(repoId, node.path)}
              className="flex w-full items-center justify-center gap-1.5 rounded-md border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)]"
            >
              <FileCode className="h-3.5 w-3.5" />
              Open file page
            </Link>
          )}
        </footer>
      )}
    </aside>
  );
}
