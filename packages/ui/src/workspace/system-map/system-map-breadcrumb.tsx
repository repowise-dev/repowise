"use client";

/**
 * Where the map is, as Estate / Group / Service, once it is anywhere but the
 * estate. Each crumb returns to its level. Built on the shared
 * `GraphScopeBreadcrumb` so every graph surface says "you are here" the same way.
 */

import type { SystemGraph } from "@repowise-dev/types";
import { GraphScopeBreadcrumb, type GraphScopeCrumb } from "../../graph/graph-scope-breadcrumb";
import type { MapLevel } from "./level-types";
import { TRAY_OPEN_TOKEN, openContainer, toEstate, type MapViewState } from "./view-state";

export interface SystemMapBreadcrumbProps {
  graph: SystemGraph;
  level: MapLevel;
  view: MapViewState;
  onViewChange: (view: MapViewState) => void;
  estateLabel?: string | undefined;
}

/** The crumbs below the estate for a view, as plain labels and target views. Exported for tests. */
export function breadcrumbTrail(
  graph: SystemGraph,
  level: MapLevel,
  view: MapViewState,
): { label: string; target: MapViewState | null }[] {
  const name = (id: string) => graph.nodes.find((n) => n.id === id)?.name ?? id;
  const groupLabel = (id: string) =>
    id === TRAY_OPEN_TOKEN ? "Not linked at runtime" : (graph.groups?.find((g) => g.id === id)?.label ?? id);
  const estate = toEstate(view);

  if (level === "between" && view.between) {
    return [{ label: `${name(view.between.a)} and ${name(view.between.b)}`, target: null }];
  }
  if (level === "focus" && view.focus) {
    const group = graph.nodes.find((n) => n.id === view.focus)?.group;
    const known = group && graph.groups?.some((g) => g.id === group);
    return [
      ...(known ? [{ label: groupLabel(group!), target: openContainer(estate, group!) }] : []),
      { label: name(view.focus), target: null },
    ];
  }
  if (level === "group" && view.open.length > 0) {
    return [{ label: groupLabel(view.open[view.open.length - 1]!), target: null }];
  }
  return [];
}

export function SystemMapBreadcrumb({ graph, level, view, onViewChange, estateLabel = "Estate" }: SystemMapBreadcrumbProps) {
  const crumbs = breadcrumbTrail(graph, level, view);
  if (crumbs.length === 0) return null;
  const leaf = crumbs[crumbs.length - 1]!;
  const trail: GraphScopeCrumb[] = crumbs.slice(0, -1).map((c) => ({
    label: c.label,
    onClick: () => c.target && onViewChange(c.target),
  }));
  return (
    <GraphScopeBreadcrumb rootLabel={estateLabel} leafLabel={leaf.label} onRoot={() => onViewChange(toEstate(view))} trail={trail} />
  );
}
