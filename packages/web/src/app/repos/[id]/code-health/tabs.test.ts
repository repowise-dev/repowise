import { describe, expect, it } from "vitest";
import { CODE_HEALTH_TABS, MOVED_TABS, movedTabHref, resolveCodeHealthTab } from "./tabs";

const sp = (q: string) => new URLSearchParams(q);

describe("resolveCodeHealthTab", () => {
  it("opens the four tabs by id and the Map by default", () => {
    for (const id of CODE_HEALTH_TABS) expect(resolveCodeHealthTab(id, null)).toBe(id);
    expect(resolveCodeHealthTab(null, null)).toBe("map");
    expect(resolveCodeHealthTab("nonsense", null)).toBe("map");
  });

  it("keeps the Map's old names working", () => {
    for (const old of ["triage", "heatmap", "hotspots", "modules", "trend"]) {
      expect(resolveCodeHealthTab(old, null)).toBe("map");
    }
  });

  it("honours the overview card's pillar link", () => {
    expect(resolveCodeHealthTab(null, "performance")).toBe("performance");
    expect(resolveCodeHealthTab("findings", "performance")).toBe("findings");
  });
});

describe("movedTabHref", () => {
  it("sends each moved tab to its new home", () => {
    expect(movedTabHref("r1", sp("tab=coverage"))).toBe("/repos/r1/tests");
    expect(movedTabHref("r1", sp("tab=security"))).toBe("/repos/r1/security");
    expect(movedTabHref("r1", sp("tab=doc-drift"))).toBe("/repos/r1/docs/drift");
    expect(movedTabHref("r1", sp("tab=impact"))).toBe("/repos/r1/review");
  });

  it("carries the parameters the new home reads", () => {
    expect(movedTabHref("r1", sp("tab=doc-drift&document=docs/a b.md"))).toBe(
      "/repos/r1/docs/drift?document=docs%2Fa%20b.md",
    );
    expect(movedTabHref("r1", sp("tab=impact&file=a.py&file=b/c.py&lens=churn"))).toBe(
      "/repos/r1/review?file=a.py&file=b%2Fc.py",
    );
  });

  it("leaves tabs that stayed alone", () => {
    for (const id of [...CODE_HEALTH_TABS, "triage"]) {
      expect(movedTabHref("r1", sp(`tab=${id}`))).toBeNull();
    }
    expect(movedTabHref("r1", sp(""))).toBeNull();
  });

  it("answers for every moved id", () => {
    for (const id of MOVED_TABS) expect(movedTabHref("r1", sp(`tab=${id}`))).not.toBeNull();
  });
});
