"use client";

/**
 * The map's chrome for the level drawn: the breadcrumb, the family chips and
 * key above the canvas, and the Between picker in the title row. The list
 * view draws no level, so it gets no breadcrumb and no chips.
 */

import type { ReactNode } from "react";
import type { SystemGraph } from "@repowise-dev/types";
import type { LevelView } from "./levels";
import { SystemMapBetweenControl } from "./system-map-between-control";
import { SystemMapBreadcrumb } from "./system-map-breadcrumb";
import { SystemMapFilters } from "./system-map-filters";
import { SystemMapLegend, type SystemMapLegendProps } from "./system-map-legend";
import type { MapLevel } from "./use-map-level";
import type { MapNavigation } from "./use-map-navigation";
import { toggleFamily, type MapViewState } from "./view-state";

export interface ChromeArgs {
  graph: SystemGraph | null;
  level: MapLevel;
  view: MapViewState;
  setView: (next: MapViewState) => void;
  nav: MapNavigation;
  legend: SystemMapLegendProps;
  estateLabel?: string | undefined;
}

export interface MapChrome {
  breadcrumb: ReactNode;
  banner: ReactNode;
  betweenControl: ReactNode;
}

function betweenControl({ graph, level, nav }: ChromeArgs): ReactNode {
  const levelView = level.levelView;
  if (!graph || !levelView || graph.nodes.length < 2) return null;
  return (
    <SystemMapBetweenControl
      graph={graph}
      families={levelView.families}
      open={nav.picker.open}
      onOpenChange={(open) => nav.setPicker(open ? { ...nav.picker, open } : { open: false })}
      from={nav.picker.from}
      onBetween={nav.onBetween}
    />
  );
}

function levelChrome(a: ChromeArgs, graph: SystemGraph, levelView: LevelView): Omit<MapChrome, "betweenControl"> {
  const breadcrumb =
    levelView.level !== "estate" ? (
      <SystemMapBreadcrumb graph={graph} level={levelView.level} view={a.view} onViewChange={a.setView} estateLabel={a.estateLabel} />
    ) : undefined;
  const banner = (
    <div className="flex min-w-0 flex-col gap-2">
      <SystemMapFilters
        familyCounts={levelView.familyCounts}
        kindCounts={a.level.kindCounts}
        families={levelView.families}
        onToggleFamily={(f) => a.setView(toggleFamily(a.view, f))}
      />
      <SystemMapLegend {...a.legend} />
    </div>
  );
  return { breadcrumb, banner };
}

export function mapChrome(a: ChromeArgs): MapChrome {
  const levelView = a.level.levelView;
  const drawn = a.nav.display === "map" && a.graph && levelView ? levelChrome(a, a.graph, levelView) : null;
  return { breadcrumb: drawn?.breadcrumb, banner: drawn?.banner, betweenControl: betweenControl(a) };
}
