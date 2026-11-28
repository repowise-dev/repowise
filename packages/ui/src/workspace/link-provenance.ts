/**
 * How a cross-repo link was matched and how a contract was found, in words a
 * reader can act on.
 *
 * The only place the workspace layer's two closed vocabularies, link
 * `strategy` and contract `origin` (`core/workspace/origins.py`), become
 * English, so the drawers, tables and prompts describe one link the same way.
 * Server-safe and pure: no React, module-scope lookups.
 */

import type { ContractOrigin, LinkStrategy } from "@repowise-dev/types/workspace";

export interface ProvenanceCopy {
  /** Terse, for a row or a fact. Sentence case, no trailing period. */
  label: string;
  /** One sentence naming the evidence. */
  meaning: string;
}

/** `satisfies` fails the build when core adds a strategy and nobody words it. */
const STRATEGIES = {
  exact_id: {
    label: "Matched by exact name",
    meaning: "Both sides name the same route, topic, table or service.",
  },
  pattern_matched: {
    label: "Matched by subscription pattern",
    meaning: "The consumer subscribes to a pattern, such as orders.*, that covers the provider's name.",
  },
  binding_routed: {
    label: "Routed through a queue binding",
    meaning:
      "The consumer's queue is bound to the exchange the provider publishes to, with a routing key it receives.",
  },
  shared_table: {
    label: "Shares a table",
    meaning: "One service only models the table; the other service's schema defines it.",
  },
  service_mapped: {
    label: "Matched by service name",
    meaning: "The call's host or base URL names the providing service, and that service serves the path.",
  },
  host_only: {
    label: "Host only",
    meaning:
      "The call's host names this service, but no route there serves its path. A service-level dependency, not a route link.",
  },
  gateway_route: {
    label: "Routed through a gateway",
    meaning: "A gateway's config forwards this path prefix to the service.",
  },
  unique_path: {
    label: "Matched by path only",
    meaning: "The call's base URL was not read, and exactly one service serves the path.",
  },
  ambiguous_path: {
    label: "Path served by several services",
    meaning:
      "The call's base URL was not read, and several services serve the path, so it is linked to each. Verify before relying on it.",
  },
  prefix_stripped: {
    label: "Matched after stripping a prefix",
    meaning: "The paths match only after removing a mount, version or base prefix. Verify before relying on it.",
  },
  param_segment: {
    label: "Matched a route parameter",
    meaning:
      "The call names a concrete value where the route declares a parameter, such as /render/1 against /render/{id}. Verify before relying on it.",
  },
  manual: {
    label: "Declared by hand",
    meaning: "Declared in the workspace config.",
  },
  manifest: {
    label: "Declared in a manifest",
    meaning: "A package manifest declares the dependency.",
  },
} as const satisfies Record<LinkStrategy, ProvenanceCopy>;

const ORIGINS = {
  idl: {
    label: "Schema document",
    meaning: "An OpenAPI or proto document declares the operation.",
  },
  package_export: {
    label: "Package export",
    meaning: "A published package exports the symbol another repo imports.",
  },
  route_registration: {
    label: "Framework registration",
    meaning: "A framework registers the route, handler, server or hub in code.",
  },
  typed_client: {
    label: "Typed client",
    meaning: "The symbol index types the receiver as an HTTP client.",
  },
  ddl: {
    label: "Schema migration",
    meaning: "SQL DDL or a migration creates or alters the table.",
  },
  explicit_table: {
    label: "Named table",
    meaning: "An ORM model names its table explicitly.",
  },
  client_call: {
    label: "Client call",
    meaning: "A known client, broker, socket or query-builder call names the destination.",
  },
  instance_base: {
    label: "Client base URL",
    meaning: "A call on a client instance whose base URL the file reads.",
  },
  dataflow: {
    label: "Traced value",
    meaning: "The destination was traced across statements to the call.",
  },
  sql_string: {
    label: "SQL in a string",
    meaning: "A table named inside SQL text in a string.",
  },
  name_heuristic: {
    label: "Name only",
    meaning: "Only the callee or receiver name says this is a client call.",
  },
  name_convention: {
    label: "Naming convention",
    meaning: "Derived from a naming convention, such as an ORM's default table name or a unique short type name.",
  },
} as const satisfies Record<ContractOrigin, ProvenanceCopy>;

/** `some_new_rule` -> `Some new rule`, so a value this build predates still reads. */
function humanise(token: string): string {
  const words = token.replace(/[_-]+/g, " ").trim();
  return words ? words[0]!.toUpperCase() + words.slice(1) : token;
}

/** Describe a link strategy. Null only when the link carries none. */
export function linkStrategyCopy(strategy: string | null | undefined): ProvenanceCopy | null {
  if (!strategy) return null;
  return (
    (STRATEGIES as Record<string, ProvenanceCopy>)[strategy] ?? {
      label: humanise(strategy),
      meaning: "This version does not describe this matching rule.",
    }
  );
}

/**
 * Strategies a reader should check before relying on the link: the call's
 * base URL was not read, or the paths matched only after loosening them.
 * Route rows name only these, so the exception stands out and the stronger
 * rules stay the quiet default.
 */
const WEAK_STRATEGIES: ReadonlySet<string> = new Set<LinkStrategy>([
  "unique_path",
  "ambiguous_path",
  "prefix_stripped",
  "param_segment",
  "host_only",
]);

export function strategyNeedsCheck(strategy: string | null | undefined): boolean {
  return Boolean(strategy && WEAK_STRATEGIES.has(strategy));
}

/** Describe a contract origin. Null only when the contract carries none. */
export function contractOriginCopy(origin: string | null | undefined): ProvenanceCopy | null {
  if (!origin) return null;
  return (
    (ORIGINS as Record<string, ProvenanceCopy>)[origin] ?? {
      label: humanise(origin),
      meaning: "This version does not describe how this contract was found.",
    }
  );
}
