/**
 * The eShop reference estate's system graph (18 services, 44 edges), cut to the
 * fields the levels model reads. Real shape: one service per repo, package
 * edges fanning into shared libraries, events both ways between catalog.api
 * and ordering.api, and one gateway-routed HTTP link.
 */

import type { LinkStrategyWire, SystemEdgeKind } from "@repowise-dev/types";
import type { LevelsGraph } from "../../../src/workspace/system-map/level-types";

type Row = [source: string, target: string, kind: string, weight: number, strategy: string];

const SERVICES = [
  "basket.api",
  "catalog.api",
  "clientapp",
  "eshop.servicedefaults",
  "eventbus",
  "eventbusrabbitmq",
  "hybridapp",
  "identity.api",
  "integrationeventlogef",
  "ordering.api",
  "ordering.domain",
  "ordering.infrastructure",
  "orderprocessor",
  "paymentprocessor",
  "webapp",
  "webappcomponents",
  "webhookclient",
  "webhooks.api",
];

const EDGES: Row[] = [
  ["basket.api", "eshop.servicedefaults", "package", 1, "manifest"],
  ["basket.api", "eventbusrabbitmq", "package", 1, "manifest"],
  ["basket.api", "ordering.api", "event", 1, "exact_id"],
  ["catalog.api", "eshop.servicedefaults", "package", 1, "manifest"],
  ["catalog.api", "eventbusrabbitmq", "package", 1, "manifest"],
  ["catalog.api", "integrationeventlogef", "package", 1, "manifest"],
  ["catalog.api", "ordering.api", "event", 2, "exact_id"],
  ["clientapp", "basket.api", "grpc", 1, "exact_id"],
  ["clientapp", "catalog.api", "http", 4, "unique_path"],
  ["clientapp", "ordering.api", "http", 4, "unique_path"],
  ["eventbusrabbitmq", "eventbus", "package", 1, "manifest"],
  ["hybridapp", "catalog.api", "http", 5, "exact_id"],
  ["identity.api", "eshop.servicedefaults", "package", 1, "manifest"],
  ["integrationeventlogef", "eventbus", "package", 1, "manifest"],
  ["ordering.api", "catalog.api", "event", 2, "exact_id"],
  ["ordering.api", "eshop.servicedefaults", "package", 1, "manifest"],
  ["ordering.api", "eventbusrabbitmq", "package", 1, "manifest"],
  ["ordering.api", "integrationeventlogef", "package", 1, "manifest"],
  ["ordering.api", "ordering.domain", "package", 1, "manifest"],
  ["ordering.api", "ordering.infrastructure", "package", 1, "manifest"],
  ["ordering.api", "orderprocessor", "event", 1, "exact_id"],
  ["ordering.api", "paymentprocessor", "event", 2, "exact_id"],
  ["ordering.infrastructure", "integrationeventlogef", "package", 1, "manifest"],
  ["ordering.infrastructure", "ordering.domain", "package", 1, "manifest"],
  ["orderprocessor", "eshop.servicedefaults", "package", 1, "manifest"],
  ["orderprocessor", "eventbusrabbitmq", "package", 1, "manifest"],
  ["paymentprocessor", "eshop.servicedefaults", "package", 1, "manifest"],
  ["paymentprocessor", "eventbusrabbitmq", "package", 1, "manifest"],
  ["paymentprocessor", "ordering.api", "event", 1, "exact_id"],
  ["webapp", "basket.api", "grpc", 2, "exact_id"],
  ["webapp", "catalog.api", "http", 2, "gateway_route"],
  ["webapp", "eshop.servicedefaults", "package", 1, "manifest"],
  ["webapp", "eventbusrabbitmq", "package", 1, "manifest"],
  ["webapp", "ordering.api", "event", 6, "exact_id"],
  ["webapp", "ordering.api", "http", 2, "service_mapped"],
  ["webapp", "webappcomponents", "package", 1, "manifest"],
  ["webappcomponents", "catalog.api", "http", 6, "exact_id"],
  ["webhookclient", "eshop.servicedefaults", "package", 1, "manifest"],
  ["webhookclient", "webhooks.api", "http", 2, "service_mapped"],
  ["webhooks.api", "catalog.api", "event", 1, "exact_id"],
  ["webhooks.api", "eshop.servicedefaults", "package", 1, "manifest"],
  ["webhooks.api", "eventbusrabbitmq", "package", 1, "manifest"],
  ["webhooks.api", "integrationeventlogef", "package", 1, "manifest"],
  ["webhooks.api", "ordering.api", "event", 2, "exact_id"],
];

export const ESHOP_GRAPH: LevelsGraph = {
  nodes: SERVICES.map((id) => ({ id, name: id, repo: id })),
  edges: EDGES.map(([source, target, kind, weight, strategy]) => ({
    id: `${source}->${target}:${kind}`,
    source,
    target,
    kind: kind as SystemEdgeKind,
    weight,
    strategy: strategy as LinkStrategyWire,
  })),
};
