import { describe, expect, it } from "vitest";
import { WORKSPACE_NAV, repoNavGroups, repoNavItems, viewIdFor } from "./nav-items";
import en from "../../../messages/en.json";
import zh from "../../../messages/zh-CN.json";

describe("repoNavGroups", () => {
  const groups = repoNavGroups("r1");

  it("groups the repo pages by the question a reader brings", () => {
    expect(groups.map((g) => [g.id, g.items.map((i) => i.label)])).toEqual([
      ["start", ["Overview", "Chat", "Review"]],
      ["understand", ["Docs", "Architecture", "Knowledge Graph", "Files"]],
      ["quality", ["Code Health", "Refactoring", "Tests", "Security"]],
      ["people", ["Ownership", "Commits", "Decisions"]],
      ["settings", ["Stats", "Usage & savings", "Settings"]],
    ]);
  });

  it("labels every group but the first, with unique ids", () => {
    expect(groups.filter((g) => !g.label).map((g) => g.id)).toEqual(["start"]);
    expect(new Set(groups.map((g) => g.id)).size).toBe(groups.length);
  });

  it("has a message for every label in both catalogs", () => {
    const keys = [
      ...repoNavItems("r1").map((i) => i.labelKey),
      ...groups.flatMap((g) => (g.labelKey ? [g.labelKey] : [])),
    ];
    for (const nav of [en.nav, zh.nav] as Record<string, string>[]) {
      expect(keys.filter((k) => !nav[k])).toEqual([]);
    }
  });

  it("links Tests and Security to their own pages", () => {
    const hrefs = repoNavItems("r1").map((i) => i.href);
    expect(hrefs).toContain("/repos/r1/tests");
    expect(hrefs).toContain("/repos/r1/security");
  });
});

describe("viewIdFor", () => {
  it("names the nav entry a path sits under, never the repo or the path", () => {
    expect(viewIdFor("/")).toBe("global.dashboard");
    expect(viewIdFor("/settings/privacy")).toBe("global.settings");
    expect(viewIdFor("/workspace")).toBe("workspace.overview");
    expect(viewIdFor("/workspace/contracts/detail")).toBe("workspace.contracts");
    expect(viewIdFor("/repos/secret-repo/code-health/trend")).toBe("repo.codeHealth");
    expect(viewIdFor("/repos/secret-repo/files/src/secret.py")).toBe("repo.files");
    expect(viewIdFor("/repos/secret-repo/settings")).toBe("repo.settings");
  });

  it("reports nothing off the nav", () => {
    expect(viewIdFor("/repos/r1")).toBeNull();
    expect(viewIdFor("/repos/r1/zoom")).toBeNull();
    expect(viewIdFor("/nowhere")).toBeNull();
  });
});

describe("workspace nav", () => {
  it("ends with the workspace Setup", () => {
    expect(WORKSPACE_NAV.at(-1)).toMatchObject({ labelKey: "setup", href: "/workspace/setup" });
  });
});
