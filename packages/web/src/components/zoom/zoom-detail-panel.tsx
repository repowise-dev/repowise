"use client";

/**
 * Detail panel for the selected zoom node: what this box is, how healthy it is,
 * what it holds, and what its arrows mean.
 *
 * It is a *rail beside the canvas*, not a floating card on it. Chrome goes
 * around a canvas, never on it: a diagram is the one thing on its page that
 * cannot be read past, so anything on top of it competes with the subject. The
 * old panel was pinned `absolute right-3 top-3` and landed on top of the search
 * box, which shares that corner.
 *
 * Inside, figures are hairline rows rather than a grid of bordered mini-cards.
 * A card means "a discrete object you can act on" and a file count is not that;
 * at 288px wide, eight bordered boxes read as box soup with no lead. Every
 * number is `tabular-nums` because a column of figures that reflows as digits
 * change fails to line up.
 */

import Link from "next/link";
import { BookOpen, FileCode, ScanSearch, X } from "lucide-react";
import type { ZoomNode, ZoomRelation } from "@repowise-dev/ui/zoom";
import {
  describeCap,
  describeRelations,
  healthBandLabel,
  KIND_LABEL,
  nodeRoles,
  summarizeRelations,
} from "@repowise-dev/ui/zoom";
import { bandForScore } from "@repowise-dev/types/health";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { pageHref } from "@/lib/utils/page-href";
import { healthBandTextColor } from "@repowise-dev/ui/health";

interface ZoomDetailPanelProps {
  node: ZoomNode;
  repoId: string;
  /** Relations incident to this node, in either direction. */
  relations: ZoomRelation[];
  /** The verb the map is currently filtered to, or null for all of them. */
  relationVerbs: ReadonlySet<string> | null;
  onClose: () => void;
  onZoom: (id: string) => void;
}

/** Route to a file's own page. */
function fileHref(repoId: string, path: string): string {
  return fileEntityPath(`/repos/${repoId}`, path);
}

/** A micro-label: mono, because it labels something a machine produced. */
function Micro({ children }: { children: React.ReactNode }) {
  return (
    <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
      {children}
    </span>
  );
}

/** One figure as a hairline row, not a card. */
function Row({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between border-t border-[var(--color-border-default)] py-1.5">
      <Micro>{label}</Micro>
      <span
        className={`text-[15px] font-medium tabular-nums ${tone ?? "text-[var(--color-text-primary)]"}`}
      >
        {value.toLocaleString()}
      </span>
    </div>
  );
}

/**
 * A role as the accent dot plus the word. The dot matches the one on the card,
 * so the panel reads as the card's caption; the word is what the card cannot
 * say. A filled pill would spend a ground, a border and coloured text on one
 * token that repeats down a panel.
 */
function Mark({ children }: { children: React.ReactNode }) {
  return (
    <span className="flex items-center gap-1 text-[11px] font-medium text-[var(--color-text-secondary)]">
      <span
        aria-hidden
        className="h-1.5 w-1.5 rounded-full bg-[var(--color-accent-primary)]"
      />
      {children}
    </span>
  );
}

export function ZoomDetailPanel({
  node,
  repoId,
  relations,
  relationVerbs,
  onClose,
  onZoom,
}: ZoomDetailPanelProps) {
  const m = node.metrics;
  const isFile = node.kind === "file";
  // The canonical 3-band scale, so the panel agrees with the dot on the card
  // beside it. The 5-step Excellent/Good ladder the scan surfaces use would
  // call a 6.9 "Good" while the card paints it amber.
  const band = healthBandLabel(node.health_score);
  const bandClass =
    node.health_score === null ? "" : healthBandTextColor(bandForScore(node.health_score));
  const roles = nodeRoles(node);
  // The description covers the whole box; the cap has to be measured against
  // what the map is *drawing*, or a filtered view still claims to be showing
  // "the 10 strongest" while two arrows are on screen.
  const summary = summarizeRelations(relations);
  const drawn = summarizeRelations(
    relationVerbs === null ? relations : relations.filter((r) => relationVerbs.has(r.label)),
  );
  const cap = describeCap(drawn);

  return (
    /* Height follows content, capped at the canvas: a panel stretched to a
       full-height rail leaves a dead gutter under a short node's figures, and
       pins its actions a long way from the thing they act on. */
    <aside className="flex max-h-full min-h-0 flex-col overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)]">
      <header className="flex items-start justify-between gap-2 px-4 pb-3 pt-3.5">
        <div className="min-w-0">
          <Micro>{KIND_LABEL[node.kind]}</Micro>
          {/* A real size step, so hierarchy comes from the type scale rather
              than from another border. */}
          <h2 className="mt-1 text-[18px] font-semibold leading-tight text-[var(--color-text-primary)]">
            {node.name}
          </h2>
          {node.path && node.path !== node.name && (
            /* Scrolls, never truncates: an ellipsis in the primary column
               reports a layout decision to the reader as missing content. */
            <p className="mt-1 overflow-x-auto whitespace-nowrap font-mono text-[11px] text-[var(--color-text-tertiary)]">
              {node.path}
            </p>
          )}
          {/* Every role the card's accent dot stands for, not just the one a
              priority cascade would have picked. */}
          {roles.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
              {roles.map((role) => (
                <Mark key={role}>{role}</Mark>
              ))}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close details"
          className="shrink-0 rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-bg-wash-hover)] hover:text-[var(--color-text-primary)]"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="min-h-0 shrink overflow-auto px-4 pb-4">
        {band && node.health_score !== null && (
          <div className="border-t border-[var(--color-border-default)] pt-3">
            <div className={`flex items-baseline gap-2 ${bandClass}`}>
              <span className="text-[32px] font-bold leading-none tabular-nums">
                {node.health_score.toFixed(1)}
              </span>
              <span className="text-[15px] font-medium">{band}</span>
            </div>
            {/* A figure alone is not readable. Say what it measures. */}
            <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--color-text-secondary)]">
              Code health out of 10
              {isFile ? "" : ", averaged across this subtree weighted by size"}.
            </p>
          </div>
        )}

        {node.summary && (
          <p className="mt-3 border-t border-[var(--color-border-default)] pt-3 text-[13px] leading-relaxed text-[var(--color-text-secondary)]">
            {node.summary}
          </p>
        )}

        <div className="mt-3">
          {!isFile && <Row label="Files" value={m.file_count} />}
          {m.hotspot_count > 0 && (
            <Row label="Hotspots" value={m.hotspot_count} tone="text-[var(--color-risk-high)]" />
          )}
          {m.entry_point_count > 0 && (
            <Row
              label="Entry points"
              value={m.entry_point_count}
              tone="text-[var(--color-success)]"
            />
          )}
          {m.on_flow_count > 0 && <Row label="On flow" value={m.on_flow_count} />}
          {m.dead_count > 0 && <Row label="Dead" value={m.dead_count} />}
        </div>

        {/* What the arrows mean. The verb was on the wire and drawn nowhere,
            so nothing on this surface said whether a line meant "imports" or
            "changes at the same time as". */}
        <div className="mt-3 border-t border-[var(--color-border-default)] pt-3">
          <Micro>Relations</Micro>
          <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--color-text-secondary)] tabular-nums">
            {describeRelations(summary)}
          </p>
          {cap && (
            /* A surface that bounds its own coverage has to say so, or the
               partial view reads as the whole one. */
            <p className="mt-1 text-[12px] text-[var(--color-text-tertiary)] tabular-nums">
              {cap} on the map.
            </p>
          )}
        </div>

        {node.language && (
          <div className="mt-3 border-t border-[var(--color-border-default)] pt-3 text-[13px] text-[var(--color-text-secondary)]">
            <Micro>Language</Micro> <span className="ml-1">{node.language}</span>
          </div>
        )}
      </div>

      {(node.children.length > 0 || isFile || node.page_id) && (
        <footer className="flex flex-col gap-2 border-t border-[var(--color-border-default)] p-3">
          {node.children.length > 0 && (
            <button
              type="button"
              onClick={() => onZoom(node.id)}
              className="flex w-full items-center justify-center gap-1.5 rounded-md bg-[var(--color-accent-primary)] px-3 py-2 text-[13px] font-medium text-[var(--color-text-on-accent)] hover:opacity-90"
            >
              <ScanSearch className="h-3.5 w-3.5" />
              Zoom in
            </button>
          )}
          {/* This card is named after a module page, so the page it is named
              after has to be reachable from it. */}
          {node.page_id && (
            <Link
              href={pageHref(repoId, node.page_id)}
              className="flex w-full items-center justify-center gap-1.5 rounded-md border border-[var(--color-border-default)] px-3 py-2 text-[13px] font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-bg-wash-hover)]"
            >
              <BookOpen className="h-3.5 w-3.5" />
              Read the module page
            </Link>
          )}
          {isFile && node.path && (
            <Link
              href={fileHref(repoId, node.path)}
              className="flex w-full items-center justify-center gap-1.5 rounded-md border border-[var(--color-border-default)] px-3 py-2 text-[13px] font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-bg-wash-hover)]"
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
