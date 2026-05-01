/**
 * Type-level tests for non-trivial contracts in @repowise/types (per
 * docs/HOSTED_IMPLEMENTATION_GUIDE.md §2.6 — "type-level test where the
 * contract is non-trivial"). These run via `vitest --typecheck` and fail at
 * tsc time if a canonical type drifts from what consumers depend on.
 *
 * Coverage focus:
 *   - ChatArtifact discriminated union: narrowing by `type` must surface
 *     the per-variant `data` shape.
 *   - GraphLink: backwards-compatible additions (edge_type, confidence) are
 *     optional, not required.
 *   - DeadCodeFinding: hosted-only enrichment fields stay optional so
 *     OSS-shaped artifacts still satisfy the contract.
 *   - DecisionRecord: status + source are union literals, not bare strings.
 *   - Hotspot vs hosted HotspotEntry: the canonical Hotspot uses `file_path`,
 *     not `path`, so a raw hosted entry should NOT be assignable.
 */

import { describe, expectTypeOf, it } from "vitest";
import type {
  ChatArtifact,
  KnownChatArtifact,
  GraphArtifact,
  HotspotArtifact,
  AnswerArtifact,
  GenericArtifact,
} from "../src/chat.js";
import type { GraphLink, GraphExport } from "../src/graph.js";
import type { DeadCodeFinding } from "../src/dead-code.js";
import type { DecisionRecord, DecisionStatus } from "../src/decisions.js";
import type { Hotspot } from "../src/git.js";

describe("ChatArtifact discriminated union", () => {
  it("narrows on .type to the per-variant data shape", () => {
    const narrow = (a: KnownChatArtifact) => {
      if (a.type === "graph") {
        expectTypeOf(a).toEqualTypeOf<GraphArtifact>();
        expectTypeOf(a.data).toEqualTypeOf<GraphExport>();
      } else if (a.type === "hotspot") {
        expectTypeOf(a).toEqualTypeOf<HotspotArtifact>();
        expectTypeOf(a.data.hotspots).toEqualTypeOf<Hotspot[]>();
      } else if (a.type === "answer") {
        expectTypeOf(a).toEqualTypeOf<AnswerArtifact>();
        expectTypeOf(a.data.confidence).toEqualTypeOf<"high" | "medium" | "low">();
      }
    };
    expectTypeOf(narrow).toBeFunction();
  });

  it("falls through to GenericArtifact for unknown tool types", () => {
    const generic: ChatArtifact = {
      type: "future_tool_we_havent_typed_yet",
      data: { whatever: 1 },
    };
    expectTypeOf(generic).toMatchTypeOf<GenericArtifact>();
  });
});

describe("GraphLink backwards compatibility", () => {
  it("requires only source/target/imported_names; v0.4.x extras are optional", () => {
    const minimal: GraphLink = {
      source: "a.ts",
      target: "b.ts",
      imported_names: [],
    };
    expectTypeOf(minimal).toEqualTypeOf<GraphLink>();
    expectTypeOf<GraphLink["edge_type"]>().toEqualTypeOf<string | undefined>();
    expectTypeOf<GraphLink["confidence"]>().toEqualTypeOf<number | undefined>();
  });
});

describe("DeadCodeFinding hosted-only enrichment", () => {
  it("treats engine-raw fields (evidence, package, etc.) as optional", () => {
    const minimal: DeadCodeFinding = {
      id: "f1",
      kind: "unreachable_file",
      file_path: "src/foo.ts",
      symbol_name: null,
      symbol_kind: null,
      confidence: 0.9,
      reason: "no inbound edges",
      lines: 12,
      safe_to_delete: true,
      primary_owner: null,
      status: "open",
      note: null,
    };
    expectTypeOf(minimal).toEqualTypeOf<DeadCodeFinding>();
    expectTypeOf<DeadCodeFinding["evidence"]>().toEqualTypeOf<
      string[] | null | undefined
    >();
  });
});

describe("DecisionRecord literal unions", () => {
  it("constrains status to the four-literal union", () => {
    expectTypeOf<DecisionStatus>().toEqualTypeOf<
      "proposed" | "active" | "deprecated" | "superseded"
    >();
    expectTypeOf<DecisionRecord["status"]>().toEqualTypeOf<DecisionStatus>();
  });
});

describe("Canonical Hotspot key shape", () => {
  it("uses file_path, not path — hosted's HotspotEntry must be adapted", () => {
    expectTypeOf<Hotspot>().toHaveProperty("file_path").toEqualTypeOf<string>();
    // Hosted backend's raw shape uses `path`. A `{ path: ... }` object should
    // NOT satisfy Hotspot; this is the contract that forces an adapter call.
    type HostedRawHotspot = { path: string };
    expectTypeOf<HostedRawHotspot>().not.toMatchTypeOf<Hotspot>();
  });
});
