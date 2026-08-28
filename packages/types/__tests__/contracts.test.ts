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
 *   - Episode: tier excludes the per-machine transcript tier, and a summary
 *     never grows a body.
 *   - Hotspot key shape: canonical Hotspot uses `file_path`, not `path`, so
 *     raw entries with a `path` key must adapt before assignment.
 */

import { describe, expect, expectTypeOf, it } from "vitest";
import type {
  ChatArtifact,
  KnownChatArtifact,
  GraphPathArtifact,
  DeadCodeArtifact,
  DiagramArtifact,
  RiskReportArtifact,
  GenericArtifact,
} from "../src/chat.js";
import type { GraphLink } from "../src/graph.js";
import type { DeadCodeFinding } from "../src/dead-code.js";
import type { DecisionRecord, DecisionStatus } from "../src/decisions.js";
import type {
  EpisodeDetail,
  EpisodeSummary,
  EpisodeTier,
} from "../src/episodes.js";
import type { Hotspot } from "../src/git.js";
import type {
  HeritageKind,
  HeritageRelation,
  SymbolHeritage,
} from "../src/symbols.js";
import type { SecurityFinding, SecuritySeverity } from "../src/security.js";
import {
  C4_IO_KINDS,
  type C4IoKind,
  type ExternalSystemEntry,
} from "../src/external-systems.js";

describe("ChatArtifact discriminated union", () => {
  it("narrows on .type to the per-variant data shape", () => {
    // Live chat registry (packages/server/.../chat_tools.py): get_overview,
    // get_context, get_risk, get_change_risk, get_why, search_codebase,
    // get_dead_code — risk_report covers get_risk + get_change_risk.
    // Legacy wire variants still narrow for stored SSE history:
    // - graph   ← removed chat tool get_dependency_path (MCP opt-in remains)
    // - diagram ← removed chat tool get_architecture_diagram
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
      } else if (a.type === "risk_report") {
        expectTypeOf(a).toEqualTypeOf<RiskReportArtifact>();
      }
    };
    expectTypeOf(narrow).toBeFunction();
  });

  it("falls through to GenericArtifact for unknown tool types", () => {
    const generic: ChatArtifact = {
      id: "artifact-1",
      version: 1,
      type: "future_tool_we_havent_typed_yet",
      tool_name: "future_tool",
      presentation: "generic",
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
  it("constrains status to the five-literal union", () => {
    // `dismissed` belongs here: the engine has always accepted it and treats it
    // differently from `deprecated` — skipped on re-extraction, hidden from
    // listings — and its absence is what left the UI sending `deprecated` for
    // a dismissal.
    expectTypeOf<DecisionStatus>().toEqualTypeOf<
      "proposed" | "active" | "deprecated" | "dismissed" | "superseded"
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
  it("severity is the three-literal union with string & {} for autocomplete", () => {
    expectTypeOf<SecuritySeverity>().toEqualTypeOf<
      "high" | "med" | "low" | (string & {})
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
      line_number: 42,
      line_verified: true,
      commit_at: null,
    };
    expectTypeOf(f.snippet).toEqualTypeOf<string | null>();
    expectTypeOf(f.detected_at).toEqualTypeOf<string>();
  });

  it("a line is nullable and always paired with its verification flag", () => {
    // Both required: an optional line_verified would let a consumer read a
    // drifted line as confirmed by omitting the flag.
    expectTypeOf<SecurityFinding["line_number"]>().toEqualTypeOf<number | null>();
    expectTypeOf<SecurityFinding["line_verified"]>().toEqualTypeOf<boolean>();
    expectTypeOf<SecurityFinding["commit_at"]>().toEqualTypeOf<string | null>();
  });
});

describe("Canonical Hotspot key shape", () => {
  it("uses file_path, not path — raw {path} entries must be adapted", () => {
    expectTypeOf<Hotspot>().toHaveProperty("file_path").toEqualTypeOf<string>();
    // Some downstream backends emit `path`. A `{ path: ... }` object should
    // NOT satisfy Hotspot; this is the contract that forces an adapter call.
    type RawPathHotspot = { path: string };
    expectTypeOf<RawPathHotspot>().not.toMatchTypeOf<Hotspot>();
  });
});

describe("C4 io_kind parity", () => {
  // Cross-language guard: these five values are the canonical IO_KINDS in the
  // Python classifier (ingestion/external_systems/io_kind.py). The Python half
  // (tests/unit/ingestion/test_io_kind.py) asserts the same membership; if one
  // side adds/removes/renames a kind without the other, one snapshot fails CI.
  it("freezes the boundary-kind set", () => {
    expect([...C4_IO_KINDS]).toEqual([
      "db",
      "network",
      "filesystem",
      "subprocess",
      "lock",
    ]);
  });

  it("derives C4IoKind from the runtime tuple", () => {
    expectTypeOf<C4IoKind>().toEqualTypeOf<
      "db" | "network" | "filesystem" | "subprocess" | "lock"
    >();
  });

  it("keeps io_kind nullable on the registry entry", () => {
    // A row with a null io_kind (untyped dep) must still satisfy the contract.
    const untyped: ExternalSystemEntry = {
      name: "left-pad",
      display_name: "Left Pad",
      ecosystem: "npm",
      category: "library",
      io_kind: null,
      version: "1.0.0",
      declared_in: "package.json",
      is_dev_dep: false,
    };
    expect(untyped.io_kind).toBeNull();
  });
});

describe("Episode tier is an allowlist, not a bare string", () => {
  it("excludes the per-machine transcript tier", () => {
    // The engine has a third tier. It never crosses HTTP, and the type is
    // where that stays true for a consumer: widening this to `string` would
    // let a component render a session somebody else's laptop recorded.
    expectTypeOf<EpisodeTier>().toEqualTypeOf<"structural" | "git">();
    expectTypeOf<EpisodeSummary["tier"]>().toEqualTypeOf<EpisodeTier>();
    expectTypeOf<EpisodeDetail["tier"]>().toEqualTypeOf<EpisodeTier>();
  });

  it("keeps a summary bodyless and a detail's verdict non-null", () => {
    // A summary that grew a `body` would mean a list route started paying
    // for one; `still_true` non-optional on a detail is what makes the
    // checked verdict impossible to forget to render.
    expectTypeOf<Extract<keyof EpisodeSummary, "body">>().toEqualTypeOf<never>();
    // Indexed rather than `keyof`, which survives both optionality and a
    // widening to `unknown` and so only ever catches outright deletion.
    expectTypeOf<EpisodeDetail["body"]>().toEqualTypeOf<string>();
    expectTypeOf<EpisodeDetail["still_true"]>().toEqualTypeOf<string>();
    expectTypeOf<EpisodeDetail["current"]>().toEqualTypeOf<boolean>();
    // Required-but-nullable, not optional: the engine field has a default and
    // pydantic serializes defaults, so the key is always on the wire. A
    // consumer discriminating "unchecked" by key presence would never see it.
    expectTypeOf<EpisodeSummary["still_true"]>().toEqualTypeOf<string | null>();
  });
});
