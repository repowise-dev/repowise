// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ZoomKind, ZoomMap, ZoomNode } from "@repowise-dev/ui/zoom";
import { ZoomSearch } from "./zoom-search";

function n(id: string, parent: string | null, kind: ZoomKind, name: string): ZoomNode {
  return {
    id,
    parent_id: parent,
    level: 0,
    kind,
    name,
    path: kind === "file" ? `src/${name}` : "",
    children: [],
    importance: 0,
    sibling_rank: 0,
    metrics: {
      file_count: 1,
      descendant_count: 0,
      hotspot_count: 0,
      dead_count: 0,
      entry_point_count: 0,
      on_flow_count: 0,
    },
    summary: "",
    language: null,
    page_id: "",
    health_score: null,
    is_entry_point: false,
    is_hotspot: false,
    is_dead: false,
    is_test: false,
    on_flow: false,
  };
}

const MAP: ZoomMap = {
  root_id: "sys",
  project_name: "repo",
  total_files: 2,
  max_depth: 2,
  truncated: false,
  nodes: [
    n("sys", null, "system", "repo"),
    n("L", "sys", "layer", "Health Engine"),
    n("F", "L", "file", "health.py"),
  ],
  relations: [],
};

describe("ZoomSearch", () => {
  afterEach(cleanup);

  it("tags results by kind and picks with the keyboard", () => {
    const onPick = vi.fn();
    render(<ZoomSearch map={MAP} onPick={onPick} />);
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "health" } });
    const options = screen.getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["Health EngineLayer", "health.pyFileHealth Engine"]);
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onPick).toHaveBeenCalledWith("F");
  });

  it("focuses on '/' from the page but not from another field", () => {
    render(
      <>
        <textarea aria-label="other" />
        <ZoomSearch map={MAP} onPick={vi.fn()} />
      </>,
    );
    const other = screen.getByLabelText("other");
    other.focus();
    fireEvent.keyDown(other, { key: "/" });
    expect(document.activeElement).toBe(other);
    fireEvent.keyDown(document.body, { key: "/" });
    expect(document.activeElement).toBe(screen.getByRole("combobox"));
  });
});
