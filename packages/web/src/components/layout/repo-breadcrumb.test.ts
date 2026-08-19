import { describe, expect, it } from "vitest";
import { getRepoBreadcrumbSegmentLabel } from "./repo-breadcrumb-label";
import { showsRouteBreadcrumb } from "./repo-breadcrumb-route";

describe("getRepoBreadcrumbSegmentLabel", () => {
  it("keeps configured route segment labels", () => {
    expect(getRepoBreadcrumbSegmentLabel("dead-code")).toBe("Dead Code");
    expect(getRepoBreadcrumbSegmentLabel("knowledge-graph")).toBe("Knowledge Graph");
    expect(getRepoBreadcrumbSegmentLabel("zoom")).toBe("Zoom Map");
  });

  it("decodes dynamic path segments for display", () => {
    expect(getRepoBreadcrumbSegmentLabel("name%40example.com")).toBe("name@example.com");
    expect(getRepoBreadcrumbSegmentLabel("name%3Ajane%20doe")).toBe("name:jane doe");
  });

  it("falls back to the raw segment for malformed escapes", () => {
    expect(getRepoBreadcrumbSegmentLabel("%E0%A4%A")).toBe("%E0%A4%A");
  });
});

describe("showsRouteBreadcrumb", () => {
  it("stands down on the docs reader, which draws its own trail", () => {
    expect(showsRouteBreadcrumb("/repos/abc/docs")).toBe(false);
    expect(showsRouteBreadcrumb("/repos/abc/docs/")).toBe(false);
  });

  it("still shows on ordinary pages under docs", () => {
    expect(showsRouteBreadcrumb("/repos/abc/docs/coverage")).toBe(true);
  });

  it("shows everywhere else", () => {
    expect(showsRouteBreadcrumb("/repos/abc")).toBe(true);
    expect(showsRouteBreadcrumb("/repos/abc/code-health")).toBe(true);
    expect(showsRouteBreadcrumb("/repos/abc/commits")).toBe(true);
  });

  it("is not fooled by a repo id that ends in the reader's segment", () => {
    expect(showsRouteBreadcrumb("/repos/my-docs/commits")).toBe(true);
    expect(showsRouteBreadcrumb("/repos/abc/docsearch")).toBe(true);
  });
});
