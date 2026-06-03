import { describe, expect, it } from "vitest";
import { getRepoBreadcrumbSegmentLabel } from "./repo-breadcrumb-label";

describe("getRepoBreadcrumbSegmentLabel", () => {
  it("keeps configured route segment labels", () => {
    expect(getRepoBreadcrumbSegmentLabel("dead-code")).toBe("Dead Code");
    expect(getRepoBreadcrumbSegmentLabel("c4")).toBe("Knowledge Graph");
  });

  it("decodes dynamic path segments for display", () => {
    expect(getRepoBreadcrumbSegmentLabel("name%40example.com")).toBe("name@example.com");
    expect(getRepoBreadcrumbSegmentLabel("name%3Ajane%20doe")).toBe("name:jane doe");
  });

  it("falls back to the raw segment for malformed escapes", () => {
    expect(getRepoBreadcrumbSegmentLabel("%E0%A4%A")).toBe("%E0%A4%A");
  });
});
