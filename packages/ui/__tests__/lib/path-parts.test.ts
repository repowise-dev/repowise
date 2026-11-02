import { describe, expect, it } from "vitest";
import { foldPackagePrefix, pathWithLine, splitDirTail, splitPath } from "../../src/lib/path-parts";

describe("splitPath", () => {
  it("splits at the last slash", () => {
    expect(splitPath("src/a/b.ts")).toEqual({ dir: "src/a", name: "b.ts" });
  });
  it("keeps a top-level file whole", () => {
    expect(splitPath("README.md")).toEqual({ dir: "", name: "README.md" });
  });
});

describe("foldPackagePrefix", () => {
  it("folds the root, src and a namespace that repeats the package", () => {
    expect(foldPackagePrefix("packages/server/src/repowise/server/mcp_server/tool.py")).toEqual({
      pkg: "server",
      rest: "mcp_server/tool.py",
    });
  });
  it("drops a directory named after the package", () => {
    expect(foldPackagePrefix("apps/web/src/web/page.tsx")).toEqual({ pkg: "web", rest: "page.tsx" });
  });
  it("treats hyphen and underscore spellings as one package", () => {
    expect(foldPackagePrefix("packages/api-client/src/api_client/x.ts")).toEqual({
      pkg: "api-client",
      rest: "x.ts",
    });
  });
  it("keeps paths outside a package root unchanged", () => {
    expect(foldPackagePrefix("src/packages/a.ts")).toEqual({ pkg: null, rest: "src/packages/a.ts" });
    expect(foldPackagePrefix("packages/README.md")).toEqual({ pkg: null, rest: "packages/README.md" });
  });
  it("never folds away the file name", () => {
    expect(foldPackagePrefix("packages/ui/src")).toEqual({ pkg: "ui", rest: "src" });
    expect(foldPackagePrefix("packages/ui/ui")).toEqual({ pkg: "ui", rest: "ui" });
  });
});

describe("splitDirTail", () => {
  it("keeps the last folder whole", () => {
    expect(splitDirTail("a/b/c")).toEqual({ head: "a/b", tail: "c" });
    expect(splitDirTail("a/b/c", 2)).toEqual({ head: "a", tail: "b/c" });
  });
  it("has no head for a single folder", () => {
    expect(splitDirTail("a")).toEqual({ head: "", tail: "a" });
    expect(splitDirTail("")).toEqual({ head: "", tail: "" });
  });
});

describe("pathWithLine", () => {
  it("adds a line or range when known", () => {
    expect(pathWithLine("a.ts", 4)).toBe("a.ts:4");
    expect(pathWithLine("a.ts", "4-9")).toBe("a.ts:4-9");
    expect(pathWithLine("a.ts", null)).toBe("a.ts");
  });
});
