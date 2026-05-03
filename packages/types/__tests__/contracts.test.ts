/**
 * Type-level tests for non-trivial contracts in @repowise-dev/types. These run
 * via `vitest --typecheck` and fail at tsc time if a canonical type drifts
 * from what consumers depend on.
 *
 * Coverage focus:
 *   - ChatArtifact discriminated union: narrowing by `type` must surface
 *     the per-variant `data` shape.
 *   - GraphLink: backwards-compatible additions (edge_type, confidence) are
 *     optional, not required.
 *   - DeadCodeFinding: optional enrichment fields stay optional so canonical
 *     artifacts still satisfy the contract without them.
 *   - DecisionRecord: status + source are union literals, not bare strings.
 *   - Hotspot key shape: canonical Hotspot uses `file_path`, not `path`, so
 *     raw entries with a `path` key must adapt before assignment.
 */

import { describe, expectTypeOf, it } from "vitest";
import type {
  ChatArtifact,
  KnownChatArtifact,
  GraphPathArtifact,
  DeadCodeArtifact,
  DiagramArtifact,
  GenericArtifact,
} from "../src/chat.js";
import type { GraphLink } from "../src/graph.js";
import type { DeadCodeFinding } from "../src/dead-code.js";
import type { DecisionRecord, DecisionStatus } from "../src/decisions.js";
import type { Hotspot } from "../src/git.js";
import type {
  HeritageKind,
  HeritageRelation,
  SymbolHeritage,
} from "../src/symbols.js";
import type { SecurityFinding, SecuritySeverity } from "../src/security.js";

describe("ChatArtifact discriminated union", () => {
  it("narrows on .type to the per-variant data shape", () => {
    // Mirrors backend reality (`backend/app/routers/chat.py:_tool_*`):
    // - get_dependency_path → { type: "graph", data: { path, distance, explanation } }
    // - get_dead_code       → { type: "dead_code", data: { high_confidence, ... } }
    // - get_architecture_diagram → { type: "diagram", data: { mermaid_syntax, ... } }
    const narrow = (a: KnownChatArtifact) => {
      if (a.type === "graph") {
        expectTypeOf(a).toEqualTypeOf<GraphPathArtifact>();
        expectTypeOf(a.data.path).toEqualTypeOf<string[]>();
        expectTypeOf(a.data.distance).toEqualTypeOf<number>();
      } else if (a.type === "dead_code") {
        expectTypeOf(a).toEqualTypeOf<DeadCodeArtifact>();
        expectTypeOf(a.data.total_findings).toEqualTypeOf<number>();
      } else if (a.type === "diagram") {
        expectTypeOf(a).toEqualTypeOf<DiagramArtifact>();
        expectTypeOf(a.data.mermaid_syntax).toEqualTypeOf<string>();
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

describe("DeadCodeFinding optional enrichment", () => {
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

describe("Heritage relation shape", () => {
  it("constrains kind to the six-literal union", () => {
    expectTypeOf<HeritageKind>().toEqualTypeOf<
      | "extends"
      | "implements"
      | "trait_impl"
      | "mixin"
      | "method_overrides"
      | "method_implements"
    >();
  });

  it("treats child_id/parent_id/confidence as optional (raw vs resolved)", () => {
    const raw: HeritageRelation = {
      child_name: "Cat",
      parent_name: "Animal",
      kind: "extends",
      line: 10,
    };
    expectTypeOf(raw).toEqualTypeOf<HeritageRelation>();
    expectTypeOf<HeritageRelation["confidence"]>().toEqualTypeOf<
      number | undefined
    >();
  });

  it("SymbolHeritage exposes both directions", () => {
    expectTypeOf<SymbolHeritage["parents"]>().toEqualTypeOf<
      HeritageRelation[]
    >();
    expectTypeOf<SymbolHeritage["children"]>().toEqualTypeOf<
      HeritageRelation[]
    >();
  });
});

describe("SecurityFinding canonical shape", () => {
  it("severity is the three-literal union widened to string", () => {
    expectTypeOf<SecuritySeverity>().toEqualTypeOf<
      "high" | "med" | "low" | string
    >();
  });

  it("snippet is nullable; detected_at is an ISO string", () => {
    const f: SecurityFinding = {
      id: 1,
      file_path: "src/auth.py",
      kind: "hardcoded_secret",
      severity: "high",
      snippet: null,
      detected_at: "2026-05-02T00:00:00Z",
    };
    expectTypeOf(f.snippet).toEqualTypeOf<string | null>();
    expectTypeOf(f.detected_at).toEqualTypeOf<string>();
  });
});

describe("Canonical Hotspot key shape", () => {
  it("uses file_path, not path â€” raw {path} entries must be adapted", () => {
    expectTypeOf<Hotspot>().toHaveProperty("file_path").toEqualTypeOf<string>();
    // Some downstream backends emit `path`. A `{ path: ... }` object should
    // NOT satisfy Hotspot; this is the contract that forces an adapter call.
    type RawPathHotspot = { path: string };
    expectTypeOf<RawPathHotspot>().not.toMatchTypeOf<Hotspot>();
  });
});
