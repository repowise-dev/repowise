"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { hierarchy, treemap, treemapSquarify, type HierarchyRectangularNode } from "d3-hierarchy";
import type { FileRow } from "@repowise-dev/types/files";
import {
  ALERT_MAX,
  bandForScore,
  HEALTH_BAND_LABEL,
  HEALTHY_MIN,
  type HealthBand,
} from "@repowise-dev/types/health";
import { ChevronRight, FolderOpen } from "lucide-react";
import { healthBandInk, healthInk } from "../health/tokens";

/**
 * `dependents` is the PageRank percentile over the import graph. It is named
 * for what it measures rather than "importance", which is a judgement the
 * number does not make: the files it ranks highest are the ones everything
 * imports — `conftest.py`, `models.py`, `cn.ts` — and calling that importance
 * invites the reader to treat a leaf utility as the thing to go read first.
 */
export type TreemapSize = "dependents" | "loc";
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

/** How many languages get their own step before the rest fall into "Other". */
const LANG_RANKED = 5;

const LANG_OTHER_INK = "var(--color-bg-inset)";
const NO_SCORE_INK = "var(--color-text-tertiary)";

/**
 * Language → fill, as one accent stepped down by how much of the repo each
 * language holds.
 *
 * What this replaces was eight hand-picked hues, two of which were
 * `--color-risk-medium` and `--color-risk-high` — the same tokens the health
 * mode paints on these same tiles. An amber square therefore meant "middling
 * health" in one mode and "Rust" in the other, on a canvas that carried no key
 * in either. Two unlabelled marks sharing a colour vocabulary is worse than one
 * unlabelled mark: a gap the reader can leave open becomes a trap, because
 * whoever correctly infers one mode has been taught a rule that makes them
 * confidently wrong about the other.
 *
 * Stepping one accent leaves the traffic lights to the mode that actually
 * carries a band, and it is the same ramp `LanguageBar` uses for the same
 * reason — proportion is the message, and naming each language is the key's
 * job. Ranks come from the whole repo rather than the drilled level so a
 * language does not change colour as you walk into a folder.
 */
function langRanks(files: FileRow[]): Map<string, number> {
  const tally = new Map<string, number>();
  for (const f of files) {
    if (f.language) tally.set(f.language, (tally.get(f.language) ?? 0) + 1);
  }
  const ordered = [...tally.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  return new Map(ordered.slice(0, LANG_RANKED).map(([lang], i) => [lang, i]));
}

function langInk(rank: number | undefined): string {
  if (rank == null) return LANG_OTHER_INK;
  if (rank === 0) return "var(--color-accent-fill)";
  return `color-mix(in srgb, var(--color-accent-fill) ${Math.max(12, 70 - rank * 16)}%, var(--color-bg-inset))`;
}

/** Health score (0-10) → the canonical band ink. Null reads neutral. */
function healthColor(score: number | null): string {
  if (score == null) return NO_SCORE_INK;
  return healthInk(score);
}

function sizeValue(row: FileRow, sizeBy: TreemapSize): number {
  if (sizeBy === "loc") return Math.max(row.loc ?? 1, 1);
  // PageRank percentile, floored so a file nothing imports still gets a sliver
  // rather than collapsing to a zero-area tile you cannot click.
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

interface KeySwatch {
  key: string;
  label: string;
  /** The band's range, or blank where the label is the whole story. */
  hint: string;
  ink: string;
}

/**
 * The canvas key, plus what area means and how much is on screen.
 *
 * Rendered by `FilesTreemap` rather than placed by the page. Both halves of
 * that are deliberate: chrome belongs *around* a canvas rather than floating on
 * it, and a caption has to come from the same source as the thing it captions —
 * a key assembled from its own second pass over the data is how you end up with
 * a sentence that no longer describes the picture above it. This one reads the
 * same `level` array the tiles are drawn from and the same functions that fill
 * them.
 *
 * The map shipped with no key at all in either colour mode, which is what let
 * the language palette quietly borrow the health tokens for a year.
 */
function KeyRow({
  level,
  ranks,
  colorBy,
  sizeBy,
  prefix,
}: {
  level: LevelChild[];
  ranks: Map<string, number>;
  colorBy: TreemapColor;
  sizeBy: TreemapSize;
  prefix: string[];
}) {
  const swatches = useMemo<KeySwatch[]>(() => {
    if (colorBy === "health") {
      // All three bands always, even where one is absent from this level: a
      // fixed scale showing two steps reads as a scale that has two.
      const out: KeySwatch[] = (["healthy", "warning", "alert"] as HealthBand[]).map((band) => ({
        key: band,
        label: HEALTH_BAND_LABEL[band],
        hint:
          band === "healthy"
            ? `${HEALTHY_MIN}+`
            : band === "alert"
              ? `< ${ALERT_MAX}`
              : `${ALERT_MAX}–${HEALTHY_MIN}`,
        ink: healthBandInk(band),
      }));
      if (level.some((c) => c.avgScore == null)) {
        out.push({
          key: "none",
          label: "Not scored",
          hint: "",
          ink: NO_SCORE_INK,
        });
      }
      return out;
    }
    // Languages are the opposite case: list the ones actually on screen, since
    // a key naming a language no tile carries is a key you have to ignore.
    const present = [...new Set(level.map((c) => c.language).filter(Boolean))];
    const out: KeySwatch[] = present
      .filter((lang) => ranks.has(lang))
      .sort((a, b) => ranks.get(a)! - ranks.get(b)!)
      .map((lang) => ({
        key: lang,
        label: lang,
        hint: "",
        ink: langInk(ranks.get(lang)),
      }));
    if (level.some((c) => !c.language || !ranks.has(c.language))) {
      out.push({ key: "other", label: "Other", hint: "", ink: LANG_OTHER_INK });
    }
    return out;
  }, [colorBy, level, ranks]);

  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-t border-[var(--color-border-default)] pt-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 font-mono text-[10px] text-[var(--color-text-tertiary)]">
        {swatches.map((s) => (
          <span key={s.key} className="inline-flex items-center">
            <span
              aria-hidden
              className="mr-1.5 inline-block h-2 w-2 rounded-sm"
              style={{ background: s.ink }}
            />
            {s.label}
            {s.hint && (
              <span className="ml-1 tabular-nums text-[var(--color-text-secondary)]">{s.hint}</span>
            )}
          </span>
        ))}
      </div>
      <p className="text-xs text-[var(--color-text-tertiary)]">
        Area is{" "}
        {sizeBy === "loc" ? "lines of code" : "how much the rest of the codebase depends on it"}.{" "}
        <span className="tabular-nums">{level.length}</span> {level.length === 1 ? "item" : "items"}{" "}
        {prefix.length > 0 ? `in ${prefix.join("/")}/` : "at the repository root"}.
      </p>
    </div>
  );
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

  // Ranked over every file, not the drilled level, so a language keeps its
  // step as you walk into a folder.
  const ranks = useMemo(() => langRanks(files), [files]);

  const fill = useCallback(
    (c: LevelChild) =>
      colorBy === "language" ? langInk(ranks.get(c.language)) : healthColor(c.avgScore),
    [colorBy, ranks],
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
                  stroke={c.isFolder ? "var(--color-border-hover)" : "none"}
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
                className="mt-0.5 font-medium tabular-nums"
                // Painted by the same function as the tile underneath the
                // pointer. It was not: this line banded at 7 while `healthInk`
                // bands at 8, so every file scoring 7.x was an amber tile with
                // a green score written on top of it.
                style={{ color: healthColor(tip.child.avgScore) }}
              >
                {HEALTH_BAND_LABEL[bandForScore(tip.child.avgScore)]} ·{" "}
                {tip.child.avgScore.toFixed(1)}
              </p>
            )}
          </div>
        )}
      </div>

      <KeyRow level={children} ranks={ranks} colorBy={colorBy} sizeBy={sizeBy} prefix={prefix} />
    </div>
  );
}
