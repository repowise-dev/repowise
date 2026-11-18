/**
 * The caption under the map: what is drawn, what the family filter hides,
 * and what the draw budget left out. Read off the drawn view itself, so the
 * words and the canvas always come from the same data. Pure, no React.
 */

import { EDGE_FAMILY_COPY, EDGE_FAMILY_ORDER } from "./edge-families";
import type { DrawnTrayNode } from "./level-types";
import type { LevelView } from "./levels";
import { plural } from "./system-map-model";

function joinWords(parts: readonly string[]): string {
  if (parts.length <= 1) return parts[0] ?? "";
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

function present(parts: readonly (string | null)[]): string[] {
  return parts.filter((p): p is string => p !== null);
}

/** "2 groups, 3 services and a tray of 4 services ...": the cards drawn. */
function cardWords(view: LevelView): string[] {
  const groups = view.nodes.filter((n) => n.type === "group" && !n.open).length;
  const services = view.nodes.filter((n) => n.type === "service").length;
  const tray = view.nodes.find((n): n is DrawnTrayNode => n.type === "tray" && !n.open);
  return present([
    groups > 0 ? plural(groups, "group", "groups") : null,
    services > 0 ? plural(services, "service", "services") : null,
    tray ? `a tray of ${plural(tray.serviceIds.length, "service", "services")} with no runtime or data link` : null,
  ]);
}

/** "3 lines between services and 2 bands carrying 17 links": the connectors drawn. */
function lineWords(view: LevelView): string[] {
  const singles = view.edges.filter((e) => e.type === "edge").length;
  let bands = 0;
  let bandLinks = 0;
  for (const e of view.edges) {
    if (e.type !== "band") continue;
    bands += 1;
    bandLinks += e.summary.links;
  }
  return present([
    singles > 0 ? `${plural(singles, "line", "lines")} between services` : null,
    bands > 0 ? `${plural(bands, "band", "bands")} carrying ${plural(bandLinks, "link", "links")}` : null,
  ]);
}

function drawnSentence(view: LevelView): string {
  const cards = cardWords(view);
  if (cards.length === 0) return "Nothing to draw at this level.";
  const lines = lineWords(view);
  return `Drawing ${joinWords(cards)}${lines.length > 0 ? `, joined by ${joinWords(lines)}` : ""}.`;
}

function focusSentence(view: LevelView, name: (id: string) => string): string | null {
  const focus = view.focus;
  if (!focus) return null;
  const count = (ids: readonly string[]) =>
    focus.columns
      .filter((c) => ids.includes(c.id))
      .reduce((sum, c) => sum + c.entries.length + c.folded.length, 0);
  const callers = count(["callers"]);
  const deps = count(["dependencies"]);
  return `Focused on ${name(focus.focus)}, ${focus.hops === 2 ? "two hops" : "one hop"}: ${plural(callers, "service depends", "services depend")} on it, and it depends on ${plural(deps, "service", "services")}.`;
}

function betweenSentence(view: LevelView, name: (id: string) => string): string | null {
  const between = view.between;
  if (!between) return null;
  const a = name(between.a);
  const b = name(between.b);
  const direct = present([
    between.aToB ? `${plural(between.aToB.links, "link", "links")} from ${a} to ${b}` : null,
    between.bToA ? `${plural(between.bToA.links, "link", "links")} from ${b} to ${a}` : null,
  ]);
  if (direct.length > 0) return `Between ${a} and ${b}: ${joinWords(direct)}.`;
  const through = new Set<string>();
  for (const p of [...between.pathsAToB, ...between.pathsBToA]) p.nodes.slice(1, -1).forEach((id) => through.add(id));
  return through.size > 0
    ? `No direct link between ${a} and ${b}; they connect through ${joinWords([...through].sort().map(name))}.`
    : `No link or two-step path between ${a} and ${b} in the families drawn.`;
}

/** "Hidden by the edge filter: 26 Build relationships (package dependencies)." Null when every family present is drawn. */
export function hiddenFamiliesSentence(view: LevelView): string | null {
  const hidden = EDGE_FAMILY_ORDER.filter((f) => !view.families.has(f) && (view.familyCounts[f]?.edges ?? 0) > 0);
  if (hidden.length === 0) return null;
  const parts = hidden.map((f) => {
    const copy = EDGE_FAMILY_COPY[f];
    return `${plural(view.familyCounts[f]!.edges, `${copy.label} relationship`, `${copy.label} relationships`)} (${copy.meaning})`;
  });
  return `Hidden by the edge filter: ${joinWords(parts)}.`;
}

/** What the draw budget folded or dropped. Null when everything fit. */
export function truncationSentence(view: LevelView): string | null {
  const t = view.truncation;
  if (!t) return null;
  const folded = [
    t.foldedServices > 0 ? plural(t.foldedServices, "service", "services") : null,
    t.foldedGroups > 0 ? plural(t.foldedGroups, "group", "groups") : null,
  ].filter((p): p is string => p !== null);
  const single = t.foldedServices + t.foldedGroups === 1;
  const parts = [
    folded.length > 0 ? `${joinWords(folded)} ${single ? "is" : "are"} folded into "+N more" cards` : null,
    t.droppedEdges > 0
      ? `the ${plural(t.droppedEdges, "weakest line", "weakest lines")} (${plural(t.droppedLinks, "link", "links")}) ${t.droppedEdges === 1 ? "is" : "are"} not drawn`
      : null,
  ].filter((p): p is string => p !== null);
  return `Over the draw budget of ${t.budget.nodes} cards and ${t.budget.edges} lines: ${joinWords(parts)}.`;
}

/** Every caption sentence for a view, in reading order. */
export function scopeCaption(view: LevelView, name: (id: string) => string): string[] {
  const level =
    view.level === "focus" ? focusSentence(view, name) : view.level === "between" ? betweenSentence(view, name) : null;
  return [
    level,
    drawnSentence(view),
    hiddenFamiliesSentence(view),
    truncationSentence(view),
    "Placement follows runtime, data and build dependencies, so changing the edge filter never moves a card.",
  ].filter((p): p is string => p !== null);
}
