import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SymbolCallGraph } from "../../src/symbols/symbol-call-graph.js";
import { SymbolPage } from "../../src/symbols/symbol-page.js";
import type {
  SymbolBodyCall,
  SymbolDetailResponse,
  SymbolRelationGroup,
} from "@repowise-dev/types/symbols";

const call = (name: string, edge_type = "calls"): SymbolBodyCall => ({
  symbol_id: `src/${name}.py::${name}`,
  name,
  file: `src/${name}.py`,
  edge_type,
  confidence: 0.9,
  resolution_origin: null,
});

const group = (
  overrides: Partial<SymbolRelationGroup<SymbolBodyCall>>,
): SymbolRelationGroup<SymbolBodyCall> => ({
  direction: "in",
  edge_type: "extends",
  group: "heritage",
  total: 1516,
  rows: [call("UserModel", "extends")],
  ...overrides,
});

const routeData = (
  graph: Partial<SymbolDetailResponse["graph"]> = {},
): SymbolDetailResponse => ({
  symbol: {
    id: "s1",
    repository_id: "r",
    file_path: "django/db/models/base.py",
    symbol_id: "django/db/models/base.py::Model",
    name: "Model",
    qualified_name: "Model",
    kind: "class",
    signature: "class Model",
    start_line: 10,
    end_line: 15,
    docstring: null,
    visibility: "public",
    is_async: false,
    complexity_estimate: 3,
    language: "python",
    parent_name: null,
    file_is_hotspot: false,
  },
  graph: {
    pagerank: 0,
    in_degree: 0,
    out_degree: 0,
    callers: [],
    callees: [],
    ...graph,
  },
  governing_decisions: [],
  file_context: {
    file_path: "django/db/models/base.py",
    health_score: 70,
    is_hotspot: false,
    primary_owner: "Ada",
    language: "python",
  },
});

describe("symbol relations", () => {
  it("reports the true caller count, not the row cap", () => {
    // The defect this pins: the server caps rows at 40 and the heading read
    // `Called by (40)` over a symbol with 1,524 inbound edges.
    render(
      <SymbolCallGraph
        centerName="Model"
        callers={[call("save"), call("delete")]}
        callees={[]}
        callerTotal={8}
      />,
    );
    expect(screen.getByText("(8)")).toBeInTheDocument();
    expect(screen.getByText("+6 more")).toBeInTheDocument();
  });

  it("falls back to the row count when the backend sends no total", () => {
    render(
      <SymbolCallGraph centerName="Model" callers={[call("save")]} callees={[]} />,
    );
    expect(screen.getByText("(1)")).toBeInTheDocument();
    expect(screen.queryByText(/more$/)).not.toBeInTheDocument();
  });

  it("names a subclass as extending, never as calling", () => {
    render(
      <SymbolCallGraph
        centerName="Model"
        callers={[call("save")]}
        callees={[]}
        callerTotal={8}
        relations={[group({})]}
      />,
    );
    expect(screen.getByText("Extended by")).toBeInTheDocument();
    expect(screen.getByText("(1,516)")).toBeInTheDocument();
    // The subclass must not appear under the call column's heading.
    expect(screen.getByText("Called by")).toBeInTheDocument();
    expect(screen.getByText("(8)")).toBeInTheDocument();
  });

  it("labels the method case differently from the class case", () => {
    // The wording half of the fix: "Extended by" is class-heritage phrasing
    // and reads as nonsense on a base method answered by implementations.
    render(
      <SymbolCallGraph
        centerName="save"
        callers={[]}
        callees={[]}
        relations={[
          group({ edge_type: "method_implements", total: 4 }),
          group({ edge_type: "implements", direction: "out", total: 1 }),
        ]}
      />,
    );
    expect(screen.getByText("Implementations")).toBeInTheDocument();
    expect(screen.getByText("Implements")).toBeInTheDocument();
    expect(screen.queryByText("Extended by")).not.toBeInTheDocument();
  });

  it("names implementations on the side the edge actually points", () => {
    // `dispatches_to` runs base -> implementation, the opposite of
    // `method_implements`. Labelling both the same way put "Implementations"
    // on the implementation instead of the base, which is the reverse the
    // gate forbids. Verified against a live index: `BaseProvider.generate`
    // carries its dispatch edges outbound.
    render(
      <SymbolCallGraph
        centerName="generate"
        callers={[]}
        callees={[]}
        relations={[
          group({ edge_type: "dispatches_to", direction: "out", total: 19 }),
          group({ edge_type: "method_implements", direction: "in", total: 19 }),
        ]}
      />,
    );
    expect(screen.getAllByText("Implementations")).toHaveLength(2);
    expect(screen.queryByText("Dispatches to")).not.toBeInTheDocument();
  });

  it("explains a relation group once, not once per verb", () => {
    render(
      <SymbolCallGraph
        centerName="Model"
        callers={[]}
        callees={[]}
        relations={[
          group({ edge_type: "extends", total: 2 }),
          group({ edge_type: "implements", total: 1 }),
        ]}
      />,
    );
    expect(screen.getAllByText(/Not a call site/)).toHaveLength(1);
  });

  it("names framework wiring rather than calling it a call", () => {
    render(
      <SymbolCallGraph
        centerName="Repo"
        callers={[]}
        callees={[]}
        relations={[
          group({ edge_type: "framework_binds", group: "wiring", total: 3 }),
        ]}
      />,
    );
    expect(screen.getByText("Wired into")).toBeInTheDocument();
    expect(screen.getByText(/no call site exists/)).toBeInTheDocument();
  });

  it("renders relations end to end from the route payload", () => {
    // The section has been dead for its whole life because nothing asserted it
    // renders. This is the assertion that fails if the endpoint stops sending
    // the key or the normaliser stops forwarding it.
    render(
      <SymbolPage
        data={routeData({
          callers: [
            {
              symbol_id: "django/db/models/query.py::get",
              name: "get",
              kind: "function",
              file: "django/db/models/query.py",
              start_line: 4,
              edge_type: "calls",
              confidence: 0.9,
              resolution_origin: null,
            },
          ],
          caller_total: 8,
          relations: [
            {
              direction: "in",
              edge_type: "extends",
              group: "heritage",
              total: 1516,
              rows: [
                {
                  symbol_id: "app/models.py::User",
                  name: "User",
                  kind: "class",
                  file: "app/models.py",
                  start_line: 1,
                  edge_type: "extends",
                  confidence: 0.9,
                  resolution_origin: null,
                },
              ],
            },
          ],
        })}
        repoId="r"
      />,
    );
    expect(screen.getByText("Extended by")).toBeInTheDocument();
    expect(screen.getByText("User")).toBeInTheDocument();
    expect(screen.getByText("(1,516)")).toBeInTheDocument();
    expect(screen.getByText("(8)")).toBeInTheDocument();
  });
});
