/**
 * The groups the map holds out by default: named with their counts in the
 * canvas footer, each a toggle, and the scope the figures cover named in words.
 */
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { LoadedNote } from "../../src/health/map/scope-note.js";
import { DEFAULT_MAP_CANVAS_LABELS } from "../../src/health/map/canvas-labels.js";
import { DEFAULT_AREA_RAIL_LABELS } from "../../src/health/area-rail/labels.js";
import { scopeName } from "../../src/health/area-map-section.js";

const held = {
  default: ["test", "docs_example", "build", "vendored", "generated"],
  counts: { test: 1755, docs_example: 27, build: 2, vendored: 0, generated: 0 },
};

describe("held groups in the canvas footer", () => {
  it("offers a toggle per held group that has files, with its count", () => {
    const onChange = vi.fn();
    const { getByRole, queryByRole } = render(
      <LoadedNote loaded={10} total={20} matches={null} labels={DEFAULT_MAP_CANVAS_LABELS} held={{ ...held, include: [], onChange }} />,
    );
    fireEvent.click(getByRole("button", { name: "Show test files (1,755)" }));
    expect(onChange).toHaveBeenCalledWith(["test"]);
    expect(getByRole("button", { name: "Show build files (2)" })).toBeInTheDocument();
    // A group with no files offers nothing to show.
    expect(queryByRole("button", { name: /vendored/ })).not.toBeInTheDocument();
  });

  it("turns a shown group back off", () => {
    const onChange = vi.fn();
    const { getByRole } = render(
      <LoadedNote loaded={10} total={20} matches={null} labels={DEFAULT_MAP_CANVAS_LABELS} held={{ ...held, include: ["test", "build"], onChange }} />,
    );
    // The verb carries the state; aria-pressed beside it read as "Hide, pressed".
    const hide = getByRole("button", { name: "Hide test files" });
    expect(hide).not.toHaveAttribute("aria-pressed");
    fireEvent.click(hide);
    expect(onChange).toHaveBeenCalledWith(["build"]);
  });
});

describe("the scope the figures cover", () => {
  it("names production code, all code, or what was brought back", () => {
    expect(scopeName(held, [], DEFAULT_AREA_RAIL_LABELS)).toBe("Production code");
    expect(scopeName(held, ["test", "docs_example", "build"], DEFAULT_AREA_RAIL_LABELS)).toBe("All code");
    expect(scopeName(held, ["test"], DEFAULT_AREA_RAIL_LABELS)).toBe("Production code and test files");
  });
});

describe("the hold on the wire", () => {
  it("sends the brought-back groups sorted, so two toggle orders are one request", async () => {
    const { holdParams } = await import("../../../api-client/src/areas.js");
    expect(holdParams({ hold: "map", include: ["test", "build"] })).toEqual({ hold: "map", include: "build,test" });
    expect(holdParams({ hold: "map", include: ["build", "test"] })).toEqual({ hold: "map", include: "build,test" });
    expect(holdParams({})).toEqual({});
  });
});
