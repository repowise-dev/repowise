import { describe, expect, it } from "vitest";
import { getRepositoryChatContext, getRepositoryChatContextQuery } from "./repository-chat-context";

const params = (entries: Record<string, string>) => new URLSearchParams(entries);

describe("getRepositoryChatContext", () => {
  it("excludes conversation workspace state from context identity", () => {
    const params = new URLSearchParams("file=src%2Fchat.ts&conversation=c1&artifact=a1&compare=a2");
    expect(getRepositoryChatContextQuery(params)).toBe("file=src%2Fchat.ts");
  });
  it("returns a repository fallback at the repo root", () => {
    expect(getRepositoryChatContext("/repos/r1")).toEqual({
      kind: "repository",
      label: "Repository",
    });
  });

  it("normalizes static product surfaces", () => {
    expect(getRepositoryChatContext("/repos/r1/architecture")).toEqual({
      kind: "architecture",
      label: "Architecture",
    });
    expect(getRepositoryChatContext("/repos/r1/dead-code")).toEqual({
      kind: "risk",
      label: "Dead Code",
    });
  });

  it("preserves decoded dynamic file targets as machine context", () => {
    expect(
      getRepositoryChatContext(
        "/repos/r1/files/packages%2Fcore/src%20file.py",
      ),
    ).toEqual({
      kind: "file",
      label: "Files",
      target: "packages/core/src file.py",
      targetKind: "path",
    });
  });

  it("reads the documentation target from query state", () => {
    expect(
      getRepositoryChatContext(
        "/repos/r1/docs",
        params({ page: "file_page:packages/core/src/index.ts" }),
      ),
    ).toEqual({
      kind: "documentation",
      label: "Docs",
      target: "file_page:packages/core/src/index.ts",
      targetKind: "documentation",
    });
  });

  it("grounds architecture chat in the selected node, focus, or module", () => {
    expect(
      getRepositoryChatContext(
        "/repos/r1/architecture",
        params({ view: "files", node: "src/api.ts", module: "src" }),
      ),
    ).toMatchObject({ target: "src/api.ts", targetKind: "path" });
    expect(
      getRepositoryChatContext(
        "/repos/r1/architecture",
        params({ module: "packages/core" }),
      ),
    ).toMatchObject({ target: "packages/core", targetKind: "module" });
  });

  it("uses the selected dependency instead of Third-party UI focus state", () => {
    expect(
      getRepositoryChatContext(
        "/repos/r1/architecture",
        params({
          view: "packages",
          package: "npm:@modelcontextprotocol/sdk",
          focus: "relationships",
        }),
      ),
    ).toMatchObject({
      target: "npm:@modelcontextprotocol/sdk",
      targetKind: "dependency",
    });
  });

  it("reflects allowlisted architecture and health view state", () => {
    expect(
      getRepositoryChatContext(
        "/repos/r1/architecture",
        params({ view: "coupling" }),
      ).label,
    ).toBe("Architecture · Coupling");
    expect(
      getRepositoryChatContext(
        "/repos/r1/code-health",
        params({ tab: "coverage", lens: "churn" }),
      ).label,
    ).toBe("Code Health · Tests");
  });

  it("grounds health and history chat in query-selected entities", () => {
    const files = new URLSearchParams();
    files.append("file", "src/a.ts");
    files.append("file", "src/b.ts");
    expect(
      getRepositoryChatContext("/repos/r1/code-health", files),
    ).toMatchObject({ target: "src/a.ts, src/b.ts", targetKind: "path" });
    expect(
      getRepositoryChatContext(
        "/repos/r1/commits",
        params({ commit: "abc123" }),
      ),
    ).toMatchObject({ target: "abc123", targetKind: "commit" });
  });

  it("uses the nested page's real subject instead of its parent route", () => {
    expect(getRepositoryChatContext("/repos/r1/docs/coverage")).toEqual({
      kind: "health",
      label: "Tests",
    });
    expect(
      getRepositoryChatContext("/repos/r1/code-health/refactoring-targets"),
    ).toEqual({
      kind: "refactoring",
      label: "Refactoring Targets",
    });
  });

  it("does not throw on malformed encoded route targets", () => {
    expect(getRepositoryChatContext("/repos/r1/symbols/%E0%A4%A")).toEqual({
      kind: "symbol",
      label: "Symbols",
      target: "%E0%A4%A",
      targetKind: "symbol",
    });
  });
});
