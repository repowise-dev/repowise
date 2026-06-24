"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { hierarchy, treemap, treemapSquarify, type HierarchyRectangularNode } from "d3-hierarchy";
import type { FileRow } from "@repowise-dev/types/files";
import { ChevronRight, FolderOpen } from "lucide-react";
import { cn } from "../lib/cn";

export type TreemapSize = "importance" | "loc";
export type TreemapColor = "health" | "language";

interface FilesTreemapProps {
  files: FileRow[];
  /** Build the per-file page href for a leaf tile. */
  fileHref: (path: string) => string;
  sizeBy: TreemapSize;
  colorBy: TreemapColor;
  /** Drill state is lifted so the toolbar breadcrumb and table can share it. */
  prefix: string[];
  onPrefixChange: (prefix: string[]) => void;
}

interface LevelChild {
  /** Display name (last path segment). */
  name: string;
  /** Full path: the file path (leaf) or the folder prefix (branch). */
  fullPath: string;
  isFolder: boolean;
  value: number;
  fileCount: number;
  /** Aggregate (file: own; folder: loc-weighted) defect score, for coloring. */
  avgScore: number | null;
  /** Dominant language across descendants, for language coloring. */
  language: string;
}

const LANG_COLORS: Record<string, string> = {
  python: "var(--color-info)",
  typescript: "var(--color-accent-secondary)",
  javascript: "var(--color-warning)",
  tsx: "var(--color-accent-secondary)",
  go: "var(--color-edge-co-change)",
  rust: "var(--color-risk-medium)",
  java: "var(--color-plum-400)",
  ruby: "var(--color-risk-high)",
};
const LANG_FALLBACK = "var(--color-text-tertiary)";

function langColor(lang: string): string {
  return LANG_COLORS[lang.toLowerCase()] ?? LANG_FALLBACK;
}

/** Health score (0-10) → traffic-light fill. Null reads neutral. */
function healthColor(score: number | null): string {
  if (score == null) return "var(--color-text-tertiary)";
  if (score < 4) return "var(--color-risk-high)";
  if (score < 7) return "var(--color-risk-medium)";
  return "var(--color-risk-low)";
}

function sizeValue(row: FileRow, sizeBy: TreemapSize): number {
  if (sizeBy === "loc") return Math.max(row.loc ?? 1, 1);
  // Importance: pagerank percentile, floored so trivial files still get a sliver.
  return Math.max(row.pagerank_pct, 1);
}

/** Direct children (folders + files) of `prefix`, aggregated from descendants. */
function levelChildren(files: FileRow[], prefix: string[], sizeBy: TreemapSize): LevelChild[] {
  const depth = prefix.length;
  const groups = new Map<string, { rows: FileRow[]; isFolder: boolean }>();
  for (const row of files) {
    const segs = row.file_path.split("/");
    // Only descendants of the current prefix.
    let matches = true;
    for (let i = 0; i < depth; i++) {
      if (segs[i] !== prefix[i]) {
        matches = false;
        break;
      }
    }
    if (!matches || segs.length <= depth) continue;
    const seg = segs[depth]!;
    const isFolder = segs.length > depth + 1;
    const existing = groups.get(seg);
    if (existing) {
      existing.rows.push(row);
      // A name is a folder if any descendant has it as a folder.
      existing.isFolder = existing.isFolder || isFolder;
    } else {
      groups.set(seg, { rows: [row], isFolder });
    }
  }

  const out: LevelChild[] = [];
  for (const [seg, { rows, isFolder }] of groups) {
    const value = rows.reduce((acc, r) => acc + sizeValue(r, sizeBy), 0);
    // loc-weighted defect average across descendants with a measured score.
    let scoreNum = 0;
    let scoreWeight = 0;
    const langTally = new Map<string, number>();
    for (const r of rows) {
      if (r.defect_score != null) {
        const w = Math.max(r.loc ?? 1, 1);
        scoreNum += r.defect_score * w;
        scoreWeight += w;
      }
      if (r.language) langTally.set(r.language, (langTally.get(r.language) ?? 0) + 1);
    }
    let domLang = "";
    let domCount = -1;
    for (const [lang, count] of langTally) {
      if (count > domCount) {
        domLang = lang;
        domCount = count;
      }
    }
    out.push({
      name: seg,
      fullPath: isFolder ? [...prefix, seg].join("/") : rows[0]!.file_path,
      isFolder,
      value,
      fileCount: rows.length,
      avgScore: scoreWeight > 0 ? scoreNum / scoreWeight : null,
      language: domLang,
    });
  }
  return out;
}

/** Treemap node datum: the root carries `children`, each leaf a `child`. */
interface TreeDatum {
  children?: TreeDatum[];
  child?: LevelChild;
}

interface Tip {
  x: number;
  y: number;
  child: LevelChild;
}

export function FilesTreemap({
  files,
  fileHref,
  sizeBy,
  colorBy,
  prefix,
  onPrefixChange,
}: FilesTreemapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ width: 640, height: 340 });
  const [tip, setTip] = useState<Tip | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      if (w > 0) {
        // Shorter on narrow (mobile) widths so the hero never dominates the fold.
        const h = w < 480 ? Math.max(200, w * 0.62) : Math.max(260, Math.min(420, w * 0.46));
        setDims({ width: w, height: h });
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const children = useMemo(
    () => levelChildren(files, prefix, sizeBy),
    [files, prefix, sizeBy],
  );

  const leaves = useMemo(() => {
    if (children.length === 0) return [] as HierarchyRectangularNode<TreeDatum>[];
    const root = hierarchy<TreeDatum>({
      children: children.map((c) => ({ child: c })),
    })
      .sum((d) => d.child?.value ?? 0)
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
    treemap<TreeDatum>()
      .size([dims.width, dims.height])
      .padding(2)
      .tile(treemapSquarify)(root);
    return root.leaves() as HierarchyRectangularNode<TreeDatum>[];
  }, [children, dims.width, dims.height]);

  const handleMove = useCallback((e: React.MouseEvent, child: LevelChild) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    setTip({ x: e.clientX - rect.left, y: e.clientY - rect.top, child });
  }, []);

  const fill = useCallback(
    (c: LevelChild) => (colorBy === "language" ? langColor(c.language) : healthColor(c.avgScore)),
    [colorBy],
  );

  if (children.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center rounded-lg border border-dashed border-[var(--color-border-default)] text-sm text-[var(--color-text-tertiary)]">
        No files to show here.
      </div>
    );
  }

  return (
    <div>
      {/* Breadcrumb */}
      <div className="mb-2 flex flex-wrap items-center gap-1 text-xs text-[var(--color-text-secondary)]">
        <button
          onClick={() => onPrefixChange([])}
          className="rounded px-1.5 py-0.5 font-medium hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
        >
          root
        </button>
        {prefix.map((seg, i) => (
          <span key={i} className="flex items-center gap-1">
            <ChevronRight className="h-3 w-3 opacity-40" />
            <button
              onClick={() => onPrefixChange(prefix.slice(0, i + 1))}
              className="rounded px-1.5 py-0.5 font-mono hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
            >
              {seg}
            </button>
          </span>
        ))}
      </div>

      <div ref={containerRef} className="relative w-full">
        <svg
          width={dims.width}
          height={dims.height}
          className="rounded-lg"
          onMouseLeave={() => setTip(null)}
        >
          {leaves.map((leaf) => {
            const c = leaf.data.child;
            if (!c) return null;
            const x0 = leaf.x0;
            const y0 = leaf.y0;
            const w = leaf.x1 - x0;
            const h = leaf.y1 - y0;
            const showLabel = w > 46 && h > 26;
            const tile = (
              <g
                onMouseMove={(e) => handleMove(e, c)}
                onMouseLeave={() => setTip(null)}
                className="cursor-pointer"
              >
                <rect
                  x={x0}
                  y={y0}
                  width={w}
                  height={h}
                  fill={fill(c)}
                  opacity={c.isFolder ? 0.55 : 0.85}
                  rx={3}
                  stroke={c.isFolder ? "var(--color-border-strong)" : "none"}
                  strokeWidth={c.isFolder ? 1 : 0}
                  className="transition-opacity hover:opacity-100"
                />
                {showLabel && (
                  <>
                    {c.isFolder && (
                      <FolderOpen
                        x={x0 + 6}
                        y={y0 + 6}
                        width={11}
                        height={11}
                        className="text-[var(--color-text-primary)]"
                      />
                    )}
                    <text
                      x={x0 + (c.isFolder ? 21 : 6)}
                      y={y0 + 15}
                      fill="var(--color-text-primary)"
                      fontSize={11}
                      fontWeight={600}
                      fontFamily="var(--font-geist-mono)"
                      pointerEvents="none"
                    >
                      {c.name.length > w / 7 ? c.name.slice(0, Math.floor(w / 7)) + "…" : c.name}
                    </text>
                    {h > 38 && (
                      <text
                        x={x0 + 6}
                        y={y0 + 28}
                        fill="color-mix(in srgb, var(--color-text-primary) 65%, transparent)"
                        fontSize={9.5}
                        pointerEvents="none"
                      >
                        {c.isFolder ? `${c.fileCount} files` : c.language || "file"}
                      </text>
                    )}
                  </>
                )}
              </g>
            );
            // Folders drill in; files navigate to the per-file page.
            return c.isFolder ? (
              <g key={c.fullPath} onClick={() => onPrefixChange([...prefix, c.name])}>
                {tile}
              </g>
            ) : (
              <a key={c.fullPath} href={fileHref(c.fullPath)}>
                {tile}
              </a>
            );
          })}
        </svg>

        {tip && (
          <div
            className="pointer-events-none absolute z-20 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] px-3 py-2 text-xs shadow-lg"
            style={{ left: Math.min(tip.x + 12, dims.width - 190), top: Math.max(tip.y - 56, 4) }}
          >
            <p className="font-mono font-medium text-[var(--color-text-primary)]">
              {tip.child.name}
            </p>
            <p className="mt-0.5 text-[var(--color-text-secondary)]">
              {tip.child.isFolder
                ? `${tip.child.fileCount} files`
                : tip.child.language || "file"}
            </p>
            {tip.child.avgScore != null && (
              <p
                className={cn(
                  "mt-0.5 font-medium",
                  tip.child.avgScore < 4
                    ? "text-[var(--color-risk-high)]"
                    : tip.child.avgScore < 7
                      ? "text-[var(--color-risk-medium)]"
                      : "text-[var(--color-risk-low)]",
                )}
              >
                health {tip.child.avgScore.toFixed(1)}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
