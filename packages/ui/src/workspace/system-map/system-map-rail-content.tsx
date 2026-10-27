"use client";

/**
 * What the rail shows for a selection: a group, a band, a service or a
 * relationship, else the level's own panel (the routes between two services
 * at the Between level). Null when there is nothing to explain, so the
 * canvas takes the full width.
 */

import type { ReactNode } from "react";
import type { NodeArchitectureRole, SystemEdge, SystemGraph, SystemNode } from "@repowise-dev/types";
import type { EdgeFamily } from "./edge-families";
import { drawnLabel } from "./flow-elements";
import type { LevelView } from "./levels";
import { BandPanel, GroupPanel } from "./system-map-aggregate-panel";
import type { LoadLinksBetween } from "./routes-between";
import { SystemMapDrawer, drawerHasSubject, type SystemMapDrawerData } from "./system-map-drawer";
import { SystemMapRoutesPanel } from "./system-map-routes-panel";
import type { RepoHealth, SystemMapSelection } from "./types";

export interface RailContentArgs {
  graph: SystemGraph;
  levelView: LevelView;
  selection: SystemMapSelection;
  nodesById: ReadonlyMap<string, SystemNode>;
  edgesById: ReadonlyMap<string, SystemEdge>;
  healthByRepo?: ReadonlyMap<string, RepoHealth> | undefined;
  roleByNodeId?: ReadonlyMap<string, NodeArchitectureRole> | undefined;
  drawer?: SystemMapDrawerData | undefined;
  loadLinksBetween?: LoadLinksBetween | undefined;
  /** The routes panel's starting filter (a route picked in search). */
  routeFilter?: string | undefined;
  onSelect: (selection: SystemMapSelection) => void;
  onOpenGroup: (groupId: string) => void;
  /** Map verbs the rail offers. */
  verbs: RailVerbs;
}

export interface RailVerbs {
  onFocus: (serviceId: string) => void;
  onRoutesFrom: (serviceId: string) => void;
  onBetween: (a: string, b: string) => void;
  onShowFamily: (family: EdgeFamily) => void;
  /** Leave the Between level. */
  onLeaveBetween: () => void;
}

/** The routes between the two services compared, when the level is Between and nothing is selected. */
function levelRail({ graph, levelView, drawer, loadLinksBetween, routeFilter, verbs }: RailContentArgs): ReactNode {
  if (levelView.level !== "between" || !levelView.between) return null;
  const { a, b } = levelView.between;
  return (
    <SystemMapRoutesPanel
      // A new pair or a new route pick starts the panel over, with that filter.
      key={`${a}|${b}|${routeFilter ?? ""}`}
      initialFilter={routeFilter}
      graph={graph}
      between={levelView.between}
      families={levelView.families}
      loadLinksBetween={loadLinksBetween}
      contractHref={drawer?.contractHref}
      contractsHref={drawer?.contractsHref}
      LinkComponent={drawer?.LinkComponent}
      onShowFamily={verbs.onShowFamily}
      onClose={verbs.onLeaveBetween}
    />
  );
}

function aggregateRail(args: RailContentArgs): ReactNode {
  const { levelView, selection, nodesById } = args;
  const lookups = { ...args, onClose: () => args.onSelect(null) };
  if (selection?.type === "group") {
    const g = levelView.nodes.find((n) => n.type === "group" && n.groupId === selection.id);
    return g?.type === "group" ? <GroupPanel {...lookups} group={g} onOpen={() => args.onOpenGroup(g.groupId)} /> : null;
  }
  if (selection?.type === "band") {
    const b = levelView.edges.find((e) => e.id === selection.id);
    if (b?.type !== "band") return null;
    const label = (id: string) => drawnLabel(levelView.nodes.find((n) => n.id === id), nodesById);
    return <BandPanel {...lookups} band={b} sourceLabel={label(b.source)} targetLabel={label(b.target)} />;
  }
  return null;
}

export function railContent(args: RailContentArgs): ReactNode {
  const aggregate = aggregateRail(args);
  if (aggregate) return aggregate;
  const { graph, selection } = args;
  if (!drawerHasSubject(graph, selection)) return levelRail(args);
  return (
    <SystemMapDrawer
      {...args.drawer}
      selection={selection}
      graph={graph}
      healthByRepo={args.healthByRepo}
      roleByNodeId={args.roleByNodeId}
      onClose={() => args.onSelect(null)}
      onSelect={args.onSelect}
      loadLinksBetween={args.loadLinksBetween}
      onFocus={args.verbs.onFocus}
      onRoutesFrom={args.verbs.onRoutesFrom}
      onBetween={args.verbs.onBetween}
    />
  );
}
