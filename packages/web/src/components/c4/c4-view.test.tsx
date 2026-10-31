// @vitest-environment jsdom

/**
 * The web wiring around the shared C4 diagram: weaker links are left off by
 * default and "Show all" brings them back, the zoom map's container kinds are
 * joined in, and the component level offers a container picker. The diagram
 * itself (React Flow + ELK) is stubbed to echo what it is handed.
 */

import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { C4DiagramProps, C4L2 } from "@repowise-dev/ui/c4";
import type { ZoomContainer } from "@repowise-dev/ui/zoom";
import { strongLinks } from "./c4-links";

const mocks = vi.hoisted(() => ({ l3Container: vi.fn() }));

const container = (id: string, file_count: number) => ({
  id,
  name: id.replace("pkg:", ""),
  path: id.replace("pkg:", ""),
  language: "python",
  file_count,
  symbol_count: 1,
  hotspot_count: 0,
  dead_count: 0,
});
const external = (id: string, category: string, io_kind: string | null = null) => ({
  id,
  name: id,
  display_name: id,
  category,
  ecosystem: "pypi",
  version: null,
  io_kind,
});
const rel = (source_id: string, target_id: string, edge_count: number, coupling: string) => ({
  source_id,
  target_id,
  label: "imports",
  edge_count,
  edge_types: ["imports"],
  coupling,
});

const L2: C4L2 = {
  containers: [container("pkg:cli", 300), container("pkg:core", 1800), { ...container("pkg:.", 2400), path: "" }],
  external_systems: [external("ext:sqlalchemy", "service", "db"), external("ext:rich", "library")],
  relations: [
    rel("pkg:cli", "pkg:core", 889, "tight"),
    rel("pkg:cli", "ext:sqlalchemy", 39, "moderate"),
    rel("pkg:cli", "ext:rich", 135, "tight"),
    rel("pkg:core", "pkg:cli", 2, "loose"),
  ],
};

vi.mock("@/lib/hooks/use-c4", () => ({
  useC4Context: () => ({
    l1: { data: undefined, isLoading: false, error: undefined },
    l2: { data: L2, isLoading: false, error: undefined },
  }),
  useC4Components: (_repo: string, id: string | null) => {
    mocks.l3Container(id);
    return { data: undefined, isLoading: false, error: undefined };
  },
}));

vi.mock("@/lib/api/c4", () => ({ getC4Mermaid: vi.fn() }));

vi.mock("@repowise-dev/ui/c4", () => ({
  C4Diagram: (props: C4DiagramProps) => (
    <div>
      <div data-testid="level">{props.level}</div>
      <div data-testid="relations">{props.l2View?.relations.length ?? 0}</div>
      <div data-testid="externals">{props.l2View?.external_systems.map((e) => e.id).join(",")}</div>
      <div data-testid="kinds">{props.l2View?.containers.map((c) => c.kind ?? "-").join(",")}</div>
      <button type="button" onClick={() => props.onLevelChange(3)}>
        to-components
      </button>
      {props.toolbarExtra}
      {props.legend}
    </div>
  ),
}));

import { C4View } from "./c4-view";

afterEach(cleanup);

const ZOOM_CONTAINERS: ZoomContainer[] = [
  { id: "pkg:cli", name: "cli", path: "cli", kind: "cli", technologies: ["Python"], file_count: 300 },
  { id: "pkg:core", name: "core", path: "core", kind: "library", technologies: ["Python"], file_count: 1800 },
];

describe("strongLinks", () => {
  it("drops loose internal links and links to libraries, and the externals they leave behind", () => {
    const { view, hidden } = strongLinks(L2);
    expect(view.relations.map((r) => r.target_id)).toEqual(["pkg:core", "ext:sqlalchemy"]);
    expect(view.external_systems.map((e) => e.id)).toEqual(["ext:sqlalchemy"]);
    expect(hidden).toBe(2);
  });
});

describe("C4View", () => {
  it("hides weaker links by default and shows them all on request", () => {
    render(<C4View repoId="r1" systemName="repowise" containers={ZOOM_CONTAINERS} />);
    expect(screen.getByTestId("level").textContent).toBe("2");
    expect(screen.getByTestId("relations").textContent).toBe("2");
    expect(screen.getByTestId("externals").textContent).toBe("ext:sqlalchemy");
    expect(screen.getByText("2 weaker links hidden")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(screen.getByTestId("relations").textContent).toBe("4");
    expect(screen.getByRole("button", { name: "Hide weaker links" })).toBeTruthy();
  });

  it("joins the zoom map's container kinds so libraries read apart", () => {
    render(<C4View repoId="r1" systemName="repowise" containers={ZOOM_CONTAINERS} />);
    expect(screen.getByTestId("kinds").textContent).toBe("cli,library,-");
  });

  it("opens components on the largest container below the root and lets another be picked", () => {
    render(<C4View repoId="r1" systemName="repowise" containers={undefined} />);
    expect(screen.queryByRole("combobox")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "to-components" }));
    const picker = screen.getByRole("combobox", { name: "Container" }) as HTMLSelectElement;
    expect(picker.value).toBe("pkg:core");
    expect(mocks.l3Container).toHaveBeenLastCalledWith("pkg:core");
    fireEvent.change(picker, { target: { value: "pkg:cli" } });
    expect(mocks.l3Container).toHaveBeenLastCalledWith("pkg:cli");
  });
});
