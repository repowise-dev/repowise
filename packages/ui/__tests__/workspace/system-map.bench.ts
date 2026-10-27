/**
 * Render-budget bench for the System Map's pure functions, over seeded
 * synthetic estates at four sizes (ten edges per service, a group per ten
 * services). Run with `npm run bench --workspace @repowise-dev/ui`.
 *
 * Add each new pure system-map function to `suite` below; the sizes and the
 * graphs are shared so every row is comparable.
 */

import { bench, describe } from "vitest";
import type { SystemEdge, SystemNode } from "@repowise-dev/types";
import { buildLevelView } from "../../src/workspace/system-map/levels";
import { computeLevelLayout, levelLayoutInput } from "../../src/workspace/system-map/level-layout";
import { toFlowElements } from "../../src/workspace/system-map/flow-elements";
import { scopeCaption } from "../../src/workspace/system-map/scope-caption";
import {
  orderByPrevious,
  planLevelLayout,
  planSignature,
  scopeSignature,
} from "../../src/workspace/system-map/layout-scopes";
import { neighbourGroups, serviceResolver } from "../../src/workspace/system-map/system-map-model";
import { allocateSlots } from "../../src/workspace/system-map/draw-budget";
import { servicesBetween } from "../../src/workspace/system-map/between";
import type { LevelsGraph } from "../../src/workspace/system-map/level-types";
import { makeSyntheticSystemGraph } from "./fixtures/synthetic-system-graph";

const SIZES = [30, 100, 300, 1000] as const;
/** ELK runs for seconds at the top sizes; a few samples are enough there. */
const SLOW = { iterations: 3, time: 0, warmupIterations: 1 } as const;
/** ELK rows stop here: past it a cold layout of the drawn estate takes minutes in Node without a worker. */
const ELK_MAX_SERVICES = 300;

for (const services of SIZES) {
  const graph = makeSyntheticSystemGraph({
    services,
    edges: services * 10,
    groups: Math.max(1, Math.round(services / 10)),
    seed: services,
  });
  const hub = graph.edges.find((e) => e.kind === "package")?.target ?? graph.nodes[0]!.id;
  // The grouped payload the levels model reads: `group` on nodes plus the `groups` list.
  const groupIds = [...new Set(graph.nodes.map((n) => n.group).filter((g): g is string => Boolean(g)))];
  const levels: LevelsGraph = { ...graph, groups: groupIds.map((id) => ({ id })) };
  const caller = graph.edges.find((e) => e.kind === "http")?.source ?? graph.nodes[0]!.id;
  const callee = graph.edges.find((e) => e.kind === "http")?.target ?? graph.nodes[1]!.id;
  const containers = groupIds.map((_, i) => ({ size: 5 + ((i * 7) % 40) }));
  // The drawn view the layout plans from: the estate with its first group open when grouped.
  const firstGroup = graph.nodes.find((n) => n.group)?.group;
  const view = buildLevelView(levels, firstGroup ? { open: [firstGroup] } : {});
  const plan = planLevelLayout(view.nodes, graph.edges);
  // Previous positions in a shuffled-ish reading order, as after a relayout.
  const previous = new Map(
    plan.root.nodes.map((n, i) => [n.id, { x: (i * 7919) % 2000, y: (i * 104729) % 1500 }] as const),
  );
  // What the canvas draws first: the estate (L0), its layout and its React Flow elements.
  const estate = buildLevelView(levels);
  // A grid stands in for ELK's positions: toFlowElements reads them, it does not depend on their values.
  const estatePositions = new Map(
    estate.nodes.map((n, i) => [n.id, { x: (i % 12) * 260, y: Math.floor(i / 12) * 140, width: 240, height: 104 }] as const),
  );
  const nodesById = new Map<string, SystemNode>(graph.nodes.map((n) => [n.id, n]));
  const edgesById = new Map<string, SystemEdge>(graph.edges.map((e) => [e.id, e]));

  describe(`system map, ${services} services / ${graph.edges.length} edges`, () => {
    bench("serviceResolver + neighbourGroups (hub, both ways)", () => {
      serviceResolver(graph);
      neighbourGroups(graph, hub, "in");
      neighbourGroups(graph, hub, "out");
    });
    bench("planLevelLayout (drawn view, edges lifted to cards)", () => {
      planLevelLayout(view.nodes, graph.edges);
    });
    bench("planSignature + scopeSignature (layout cache keys)", () => {
      planSignature(plan);
      for (const s of plan.interiors) scopeSignature(s);
    });
    bench("orderByPrevious (root scope)", () => {
      orderByPrevious(plan.root.nodes, previous);
    });
    bench("buildLevelView estate (cold payload, normalizes)", () => {
      buildLevelView({ ...levels });
    });
    bench("buildLevelView open group (cached payload)", () => {
      buildLevelView(levels, { open: groupIds.slice(0, 1) });
    });
    bench("buildLevelView focus, two hops (cached payload)", () => {
      buildLevelView(levels, { focus: hub, hops: 2 });
    });
    bench("allocateSlots (one container per group)", () => {
      allocateSlots(containers, 119);
    });
    bench("servicesBetween (runtime pair)", () => {
      servicesBetween(levels, caller, callee);
    });
    if (services <= ELK_MAX_SERVICES) {
      bench("L0 layout: computeLevelLayout estate (ELK over the drawn view, cold cache)", async () => {
        await computeLevelLayout(levelLayoutInput(buildLevelView(levels), graph.edges), { worker: false });
      }, SLOW);
      bench("open group layout: computeLevelLayout with one group open (cold cache)", async () => {
        await computeLevelLayout(levelLayoutInput(view, graph.edges), { worker: false });
      }, SLOW);
    }
    bench("toFlowElements estate (cards, bands, health join)", () => {
      toFlowElements({ view: estate, positions: estatePositions, nodesById, edgesById });
    });
    bench("scopeCaption estate", () => {
      scopeCaption(estate, (id) => id);
    });
  });
}
