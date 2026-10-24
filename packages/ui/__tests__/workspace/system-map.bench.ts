/**
 * Render-budget bench for the System Map's pure functions, over seeded
 * synthetic estates at four sizes (ten edges per service, a group per ten
 * services). Run with `npm run bench --workspace @repowise-dev/ui`.
 *
 * Add each new pure system-map function to `suite` below; the sizes and the
 * graphs are shared so every row is comparable.
 */

import { bench, describe } from "vitest";
import type { SystemEdgeKind } from "@repowise-dev/types";
import { applyView, computeSystemMapPositions, layoutSignature } from "../../src/workspace/system-map/layout";
import { collapseToRepos } from "../../src/workspace/system-map/collapse";
import { neighbourGroups, serviceResolver } from "../../src/workspace/system-map/system-map-model";
import { makeSyntheticSystemGraph } from "./fixtures/synthetic-system-graph";

const SIZES = [30, 100, 300, 1000] as const;
const RUNTIME_KINDS = new Set<SystemEdgeKind>(["http", "grpc", "socket", "event", "db"]);
/** ELK runs for seconds at the top sizes; a few samples are enough there. */
const SLOW = { iterations: 3, time: 0, warmupIterations: 1 } as const;

for (const services of SIZES) {
  const graph = makeSyntheticSystemGraph({
    services,
    edges: services * 10,
    groups: Math.max(1, Math.round(services / 10)),
    seed: services,
  });
  const hub = graph.edges.find((e) => e.kind === "package")?.target ?? graph.nodes[0]!.id;

  describe(`system map, ${services} services / ${graph.edges.length} edges`, () => {
    bench("applyView (runtime kinds)", () => {
      applyView(graph, { visibleKinds: RUNTIME_KINDS, collapsed: false });
    });
    bench("collapseToRepos", () => {
      collapseToRepos(graph);
    });
    bench("layoutSignature", () => {
      layoutSignature(graph);
    });
    bench("serviceResolver + neighbourGroups (hub, both ways)", () => {
      serviceResolver(graph, false);
      neighbourGroups(graph, hub, "in");
      neighbourGroups(graph, hub, "out");
    });
    bench("computeSystemMapPositions (ELK, grid past 500)", async () => {
      await computeSystemMapPositions(graph);
    }, SLOW);
  });
}
