"use client";

/**
 * Detail panel for the selected zoom node: what this box is, how healthy it is,
 * what it holds, and what its arrows mean.
 *
 * The page shows it as a dismissible sheet on the canvas's right edge. Its
 * first home there landed on the search box sharing that corner; its second, a
 * grid column beside the canvas, resized the canvas and slid every card under
 * the pointer. As a sheet, search lives in the header and the canvas keeps its
 * size, panning a selected card out from under the sheet when needed.
 *
 * Inside, figures are hairline rows rather than a grid of bordered mini-cards.
 * A card means "a discrete object you can act on" and a file count is not that;
 * at 288px wide, eight bordered boxes read as box soup with no lead. Every
 * number is `tabular-nums` because a column of figures that reflows as digits
 * change fails to line up.
 */

import { ScanSearch, X } from "lucide-react";
import type { ZoomNode, ZoomRelation } from "@repowise-dev/ui/zoom";
import {
  describeCap,
  describeRelations,
  healthBandLabel,
  KIND_LABEL,
  nodeRoles,
  summarizeRelations,
} from "@repowise-dev/ui/zoom";
import { bandForScore, formatScore } from "@repowise-dev/types/health";
import { healthBandTextColor } from "@repowise-dev/ui/health";
import { useTranslations } from "next-intl";
import { ZoomDetailC4 } from "./zoom-detail-c4";
import { ZoomDetailActions } from "./zoom-detail-actions";
import { formatNumber } from "@repowise-dev/ui/lib/format";

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
        {formatNumber(value)}
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
  const t = useTranslations("zoom");
  const m = node.metrics;
  const isFile = node.kind === "file";
  // The shared bands, so the panel agrees with the dot on the card beside it.
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
          aria-label={t("detail.close")}
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
                {formatScore(node.health_score)}
              </span>
              <span className="text-[15px] font-medium">{band}</span>
            </div>
            {/* A figure alone is not readable. Say what it measures. */}
            <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--color-text-secondary)]">
              {isFile
                ? t("detail.healthOutOfTen")
                : t("detail.healthOutOfTenSubtree")}
            </p>
          </div>
        )}

        {node.summary && (
          <p className="mt-3 border-t border-[var(--color-border-default)] pt-3 text-[13px] leading-relaxed text-[var(--color-text-secondary)]">
            {node.summary}
          </p>
        )}

        <div className="mt-3">
          {!isFile && <Row label={t("detail.files")} value={m.file_count} />}
          {m.hotspot_count > 0 && (
            <Row
              label={t("detail.hotspots")}
              value={m.hotspot_count}
              tone="text-[var(--color-risk-high)]"
            />
          )}
          {m.entry_point_count > 0 && (
            <Row
              label={t("detail.entryPoints")}
              value={m.entry_point_count}
              tone="text-[var(--color-success)]"
            />
          )}
          {m.on_flow_count > 0 && (
            <Row label={t("detail.onFlow")} value={m.on_flow_count} />
          )}
          {m.dead_count > 0 && <Row label={t("detail.dead")} value={m.dead_count} />}
        </div>

        {/* What the arrows mean. The verb was on the wire and drawn nowhere,
            so nothing on this surface said whether a line meant "imports" or
            "changes at the same time as". */}
        <div className="mt-3 border-t border-[var(--color-border-default)] pt-3">
          <Micro>{t("detail.relations")}</Micro>
          <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--color-text-secondary)] tabular-nums">
            {describeRelations(summary)}
          </p>
          {cap && (
            /* A surface that bounds its own coverage has to say so, or the
               partial view reads as the whole one. */
            <p className="mt-1 text-[12px] text-[var(--color-text-tertiary)] tabular-nums">
              {t("detail.capSuffix", { cap })}
            </p>
          )}
        </div>

        <ZoomDetailC4 node={node} />
        {node.language && (
          <div className="mt-3 border-t border-[var(--color-border-default)] pt-3 text-[13px] text-[var(--color-text-secondary)]">
            <Micro>{t("detail.language")}</Micro>{" "}
            <span className="ml-1">{node.language}</span>
          </div>
        )}
      </div>

      <footer className="flex flex-col gap-2 border-t border-[var(--color-border-default)] p-3">
        {node.children.length > 0 && (
          <button
            type="button"
            onClick={() => onZoom(node.id)}
            className="flex w-full items-center justify-center gap-1.5 rounded-md bg-[var(--color-action-primary)] px-3 py-2 text-[13px] font-medium text-[var(--color-text-on-action)] hover:bg-[var(--color-action-primary-hover)]"
          >
            <ScanSearch className="h-3.5 w-3.5" />
            {t("detail.zoomIn")}
          </button>
        )}
        <ZoomDetailActions node={node} repoId={repoId} />
      </footer>
    </aside>
  );
}
