import { describe, it, expect } from "vitest";
import {
  ESTATE_VIEW,
  TRAY_OPEN_TOKEN,
  backOut,
  closeContainer,
  openContainer,
  parseMapView,
  toEstate,
  toLevelViewState,
  toggleFamily,
  writeMapView,
  type MapViewState,
} from "../../src/workspace/system-map/view-state";

const parse = (query: string) => {
  const p = new URLSearchParams(query);
  return parseMapView((k) => p.get(k));
};

describe("map view state in the URL", () => {
  it("reads open, focus, a, b and kinds", () => {
    expect(parse("open=tag:ordering,tray&focus=ordering.api&a=webapp&b=ordering.api&kinds=build,runtime")).toEqual({
      open: ["tag:ordering", "tray"],
      focus: "ordering.api",
      between: { a: "webapp", b: "ordering.api" },
      // Family order is fixed, whatever order the URL lists them in.
      families: ["runtime", "build"],
    });
  });

  it("reads an empty query as the estate, and ignores what names nothing", () => {
    expect(parse("")).toEqual(ESTATE_VIEW);
    // Unknown families read as the default, not as "draw nothing"; a lone `a` or a == b is no comparison.
    expect(parse("kinds=telepathy&a=x").families).toBeNull();
    expect(parse("a=x&b=x").between).toBeNull();
    expect(parse("open=g1,,g1").open).toEqual(["g1"]);
  });

  it("writes only what differs from the estate, and round-trips", () => {
    const state: MapViewState = {
      open: ["tag:ordering"],
      focus: null,
      between: { a: "webapp", b: "ordering.api" },
      families: ["runtime", "data", "build"],
    };
    const params = new URLSearchParams("lens=core&open=stale");
    writeMapView(params, state);
    expect(params.get("lens")).toBe("core");
    expect(params.toString()).toContain("open=tag%3Aordering");
    expect(params.get("kinds")).toBe("runtime,data,build");
    expect(parseMapView((k) => params.get(k))).toEqual(state);

    const estate = new URLSearchParams("open=g&kinds=runtime,data&focus=x");
    writeMapView(estate, { ...ESTATE_VIEW, families: ["runtime", "data"] });
    // The default families need no param.
    expect(estate.toString()).toBe("");
  });
});

describe("level transitions", () => {
  it("opens a group, focuses, compares, then backs out one level at a time", () => {
    let s = openContainer(ESTATE_VIEW, "g1");
    s = openContainer(s, "g2");
    s = { ...s, focus: "svc" };
    s = { ...s, between: { a: "svc", b: "other" } };
    const steps: MapViewState[] = [];
    for (let up = backOut(s); up; up = backOut(up)) steps.push(up);
    expect(steps.map((v) => [v.between ? "between" : "", v.focus ?? "", v.open.join(",")])).toEqual([
      ["", "svc", "g1,g2"],
      ["", "", "g1,g2"],
      ["", "", "g1"],
      ["", "", ""],
    ]);
    expect(backOut(ESTATE_VIEW)).toBeNull();
  });

  it("reopening a group moves it last, so Escape closes it first", () => {
    const s = openContainer(openContainer(openContainer(ESTATE_VIEW, "g1"), "g2"), "g1");
    expect(s.open).toEqual(["g2", "g1"]);
    expect(closeContainer(s, "g2").open).toEqual(["g1"]);
  });

  it("keeps the families when going back to the estate", () => {
    const s = toggleFamily(openContainer(ESTATE_VIEW, "g1"), "build");
    expect(s.families).toEqual(["runtime", "data", "build"]);
    expect(toEstate(s)).toEqual({ ...ESTATE_VIEW, families: ["runtime", "data", "build"] });
    expect(toggleFamily(s, "runtime").families).toEqual(["data", "build"]);
  });

  it("keeps the last drawn family, so the filter never empties the map", () => {
    const one = toggleFamily(ESTATE_VIEW, "data");
    expect(one.families).toEqual(["runtime"]);
    expect(toggleFamily(one, "runtime")).toBe(one);
  });

  it("hands the tray to the levels model separately from groups", () => {
    const level = toLevelViewState(openContainer(openContainer(ESTATE_VIEW, "g1"), TRAY_OPEN_TOKEN));
    expect([...(level.open ?? [])]).toEqual(["g1"]);
    expect(level.openTray).toBe(true);
    expect([...level.families!].sort()).toEqual(["data", "runtime"]);
  });
});
