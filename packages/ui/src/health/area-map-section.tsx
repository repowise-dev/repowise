"use client";

/**
 * The area map and its context rail, as one section of the Code Health page.
 *
 * With the areas API on the adapter, the hierarchy and its files load as the
 * reader zooms, and the rail reads the selection one level at a time: the
 * repository, an area, a file. A host without it hands in a flat feed, the
 * map derives the same hierarchy from it, and the rail falls back to the
 * ranked file list with the file drawer beside it.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { Search } from "lucide-react";
import type { AreaHeld } from "@repowise-dev/types/areas";
import type { HealthMapFeed } from "@repowise-dev/types/health";

import { ApiError } from "../shared/api-error";
import { toFriendlyMessage } from "../lib/errors";
import { Skeleton } from "../ui/skeleton";
import { OverviewSection } from "../overview/section";
import { ROOT_AREA } from "../lib/areas";
import { CodeHealthMap, MapLegend, MapLensSwitcher, type CodeHealthMapFile, type CodeHealthOverlay, type MapScope } from "./code-health-map";
import { deriveAreaIndex, type AreaIndex } from "./map/areas";
import { OVERLAY_SPECS, resolveLens } from "./map/lens-registry";
import { useAreaSource, type AreaFetchers } from "./map/use-area-source";
import { MapFieldList } from "./map/inspector";
import { AreaRail } from "./area-rail/area-rail";
import { DEFAULT_AREA_RAIL_LABELS, type AreaRailLabels } from "./area-rail/labels";
import type { CodeHealthAdapter } from "./code-health-adapter";

/** Map height. The rail is height-matched so it never outgrows the field beside it. */
const MAP_HEIGHT = 720;
/** The breadcrumb above the map and the legend under it, so the rail ends with them. */
const CHROME_ROWS = 80;

/** Optional flat-feed controls, passed through to the map untouched. */
export type FlatMapExtras = Partial<
  Pick<
    Parameters<typeof CodeHealthMap>[0],
    "lenses" | "overlayLoading" | "scope" | "highlightPaths" | "onDrawMore" | "drawMoreStep" | "onIncludeHeldChange" | "onFindUndrawn" | "pending"
  >
>;

export interface AreaMapSectionProps {
  adapter: CodeHealthAdapter;
  overlay: CodeHealthOverlay;
  onOverlayChange?: ((overlay: CodeHealthOverlay) => void) | undefined;
  lenses?: CodeHealthOverlay[] | undefined;
  /** Lenses shown as segments; the rest sit behind "More". All, when absent. */
  primaryLenses?: CodeHealthOverlay[] | undefined;
  /** The flat feed, for a host without the areas API. */
  mapFeed?: HealthMapFeed | undefined;
  mapScope?: MapScope | undefined;
  mapError?: unknown;
  onRetryMap?: (() => void) | undefined;
  mapExtra: FlatMapExtras;
  overlayLoading: boolean;
  selected: string | null;
  setSelected: (path: string | null) => void;
  /** Selected area, owned by the host so it rides in the URL. Local otherwise. */
  area?: string | undefined;
  onAreaChange?: ((area: string) => void) | undefined;
  /** Per-file signals the host joins onto the loaded rows (e.g. churn). */
  fileExtras?: ReadonlyMap<string, Partial<CodeHealthMapFile>> | undefined;
  railLabels?: Partial<AreaRailLabels> | undefined;
  /**
   * Held-out groups brought back (`["test"]`), owned by the host so it rides
   * in the URL. With `onIncludeChange` the canvas footer offers a toggle per
   * held group; the host's fetchers read the same list.
   */
  include?: readonly string[] | undefined;
  onIncludeChange?: ((include: string[]) => void) | undefined;
  /** Overrides the labels' description for this host. */
  description?: string | undefined;
}

/** The areas fetchers, stable across renders: most hosts rebuild the adapter every render. */
function useFetchers(adapter: CodeHealthAdapter): AreaFetchers | null {
  const ref = useRef(adapter);
  ref.current = adapter;
  const has = Boolean(adapter.getAreaTree && adapter.getAreaFiles && adapter.getAreaDetail);
  return useMemo(
    () =>
      has
        ? {
            getAreaTree: (root: string, depth: number) => ref.current.getAreaTree!(root, depth),
            getAreaFiles: (area: string) => ref.current.getAreaFiles!(area),
          }
        : null,
    [has],
  );
}

/** What the map's figures cover, given the held groups brought back. */
export function scopeName(
  held: { default: readonly string[]; counts: Readonly<Record<string, number>> },
  include: readonly string[],
  labels: AreaRailLabels,
): string {
  const present = held.default.filter((o) => (held.counts[o] ?? 0) > 0);
  const back = present.filter((o) => include.includes(o));
  if (back.length === 0) return labels.scopeProduction;
  if (back.length === present.length) return labels.scopeAll;
  return labels.scopeWith(back.map((o) => labels.originName(o)).join(", "));
}

function SearchBox({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  return (
    <div className="relative">
      <Search
        aria-hidden="true"
        className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-tertiary)]"
      />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={label}
        aria-label={label}
        className="w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] py-1.5 pl-7 pr-2 text-xs text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] hover:border-[var(--color-border-hover)] focus-visible:border-[var(--color-accent-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
      />
    </div>
  );
}

/** The lazily loaded source with the host's per-file extras joined on, or none without the areas API. */
function useJoinedSource(
  adapter: CodeHealthAdapter,
  fileExtras: AreaMapSectionProps["fileExtras"],
  holdKey: string,
) {
  const fetchers = useFetchers(adapter);
  // The held groups change which files every read covers, so they start the
  // hierarchy over, like a new repository.
  const raw = useAreaSource(fetchers, `${adapter.cacheKey}|${holdKey}`);
  const files = useMemo(
    () =>
      fileExtras
        ? raw.files.map((f) => {
            const extra = fileExtras.get(f.file_path);
            return extra ? { ...f, ...extra } : f;
          })
        : raw.files,
    [raw.files, fileExtras],
  );
  return fetchers ? { ...raw, files } : undefined;
}

/**
 * The selected area, the host's when it owns it. A file belongs to the level
 * it was opened from, so changing area drops it; a host that owns the area
 * drops it in the same URL write, since a second write clearing the file
 * separately would race the first and undo it.
 */
function useAreaSelection(
  owned: string | undefined,
  onAreaChange: ((area: string) => void) | undefined,
  setSelected: (path: string | null) => void,
) {
  const [local, setLocal] = useState(ROOT_AREA);
  const setArea = useCallback(
    (next: string) => {
      if (onAreaChange) onAreaChange(next);
      else {
        setLocal(next);
        setSelected(null);
      }
    },
    [onAreaChange, setSelected],
  );
  return [owned ?? local, setArea] as const;
}

type MapPaneProps = Omit<Parameters<typeof CodeHealthMap>[0], "minHeight" | "chrome"> & {
  failed: unknown;
  waiting: boolean;
  onRetry?: (() => void) | undefined;
  errorTitle: string;
};

/** The canvas, or the state standing in for it while it loads or after it failed. */
function MapPane({ failed, waiting, onRetry, errorTitle, ...map }: MapPaneProps) {
  if (failed) {
    return (
      <div className="flex items-center justify-center" style={{ minHeight: MAP_HEIGHT }}>
        <ApiError title={errorTitle} message={toFriendlyMessage(failed)} {...(onRetry ? { onRetry } : {})} />
      </div>
    );
  }
  if (waiting) return <Skeleton className="w-full rounded-xl" style={{ height: MAP_HEIGHT }} />;
  return <CodeHealthMap {...map} minHeight={MAP_HEIGHT} chrome="none" />;
}

/** The held groups the host brought back, as the canvas footer offers them and the rail names them. */
function heldScope(
  held: AreaHeld | null,
  include: readonly string[],
  onIncludeChange: ((include: string[]) => void) | undefined,
  labels: AreaRailLabels,
) {
  const heldGroups =
    held && onIncludeChange ? { default: held.default, counts: held.counts, include, onChange: onIncludeChange } : undefined;
  return { heldGroups, scope: held ? scopeName(held, include, labels) : undefined };
}

/** The rail: the area cards with the areas API, the ranked file list without. */
function SectionRail(
  props: AreaMapSectionProps & {
    source: AreaSourceLike | undefined;
    index: AreaIndex | null;
    flatFiles: CodeHealthMapFile[] | undefined;
    area: string;
    setArea: (area: string) => void;
    labels: AreaRailLabels;
    scope: string | undefined;
    holdKey: string;
    heldOut: boolean;
  },
) {
  const { adapter, overlay, selected, setSelected, area, index, labels } = props;
  if (props.source && adapter.getAreaDetail && index) {
    return (
      <AreaRail
        cacheKey={adapter.cacheKey}
        getAreaDetail={(p) => adapter.getAreaDetail!(p)}
        area={area}
        index={index}
        nameOf={(p) => (area && p.startsWith(`${area}/`) ? p.slice(area.length + 1) : p)}
        labels={labels}
        selectedFile={selected}
        fileDetail={adapter.renderFileDrawer({ filePath: selected, onClose: () => setSelected(null), lens: overlay, inline: true })}
        onClearFile={() => setSelected(null)}
        onSelectArea={props.setArea}
        onSelectFile={setSelected}
        areaLinks={adapter.areaLinks ? (p) => adapter.areaLinks!(p) : undefined}
        onNavigate={(href) => adapter.navigate(href)}
        scope={props.scope}
        holdKey={props.holdKey}
        {...(props.heldOut ? { errorHint: labels.heldOutHint } : {})}
      />
    );
  }
  if (!props.flatFiles) return null;
  return (
    <div className="min-h-0 flex-1 overflow-hidden border-t border-[var(--color-border-default)] pt-3">
      <MapFieldList
        files={props.flatFiles}
        overlay={overlay}
        selectedPath={selected}
        onSelectFile={setSelected}
        {...(props.mapScope ? { scope: props.mapScope } : {})}
      />
    </div>
  );
}

type AreaSourceLike = ReturnType<typeof useJoinedSource>;

export function AreaMapSection(props: AreaMapSectionProps) {
  const { overlay, selected, setSelected } = props;
  const labels = useMemo(() => ({ ...DEFAULT_AREA_RAIL_LABELS, ...props.railLabels }), [props.railLabels]);
  const include = props.include ?? [];
  // Sorted, so two orders of the same toggles are one read.
  const holdKey = [...include].sort().join(",");
  const source = useJoinedSource(props.adapter, props.fileExtras, holdKey);
  const { heldGroups, scope } = heldScope(source?.held ?? null, include, props.onIncludeChange, labels);
  const [area, setArea] = useAreaSelection(props.area, props.onAreaChange, setSelected);
  const [query, setQuery] = useState("");

  const flatFiles = props.mapFeed?.files as CodeHealthMapFile[] | undefined;
  const derived = useMemo(() => (source || !flatFiles ? null : deriveAreaIndex(flatFiles)), [source, flatFiles]);
  const index = source?.index ?? derived;
  const lens = useMemo(() => (index ? resolveLens(overlay, index, labels) : undefined), [overlay, index, labels]);

  const rail = (
    <SectionRail
      {...props}
      source={source}
      index={index}
      flatFiles={flatFiles}
      area={area}
      setArea={setArea}
      labels={labels}
      scope={scope}
      holdKey={holdKey}
      heldOut={Boolean(heldGroups)}
    />
  );

  return (
    <OverviewSection
      title={labels.mapTitle}
      description={props.description ?? (source ? labels.mapDescription : labels.flatMapDescription)}
      action={
        props.onOverlayChange ? (
          <MapLensSwitcher
            overlay={overlay}
            onOverlayChange={props.onOverlayChange}
            {...(props.lenses ? { lenses: props.lenses } : {})}
            {...(props.primaryLenses ? { primary: props.primaryLenses } : {})}
            moreLabel={labels.moreLenses}
            {...(labels.lensName ? { nameOf: (l: CodeHealthOverlay) => labels.lensName!(l) ?? OVERLAY_SPECS[l].label } : {})}
          />
        ) : undefined
      }
    >
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="flex min-w-0 flex-col gap-2.5">
          <MapPane
            failed={source ? source.error : !flatFiles && props.mapError}
            waiting={source ? source.loading : !flatFiles}
            onRetry={source ? source.retry : props.onRetryMap}
            errorTitle={labels.mapLoadError}
            ariaLabel={labels.mapAriaLabel}
            rootLabel={labels.repository}
            labels={labels}
            files={flatFiles ?? []}
            {...(source ? { source } : {})}
            area={area}
            onAreaChange={setArea}
            search={query}
            selectedPath={selected}
            onSelectFile={setSelected}
            overlay={overlay}
            {...(lens ? { lens } : {})}
            {...(heldGroups ? { heldGroups } : {})}
            {...props.mapExtra}
          />
          <MapLegend overlay={overlay} {...(lens ? { lens } : {})} loading={props.overlayLoading} />
        </div>
        {/* Separated from the canvas by space, not a rule: a vertical hairline
            down the side of the canvas would turn the map into a trench. */}
        <aside
          aria-label="Map selection"
          className="flex min-w-0 flex-col gap-4 lg:sticky lg:top-4 lg:h-[var(--rail-h)] lg:overflow-y-auto lg:pr-1"
          style={{ "--rail-h": `${MAP_HEIGHT + CHROME_ROWS}px` } as CSSProperties}
        >
          <SearchBox value={query} onChange={setQuery} label={labels.searchPlaceholder} />
          {rail}
        </aside>
      </div>
    </OverviewSection>
  );
}
