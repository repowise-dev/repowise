import { describe, expect, it } from "vitest";
import { createTranslator } from "next-intl";
import { workspaceDocFreshness } from "./doc-freshness";
import en from "../../../messages/en.json";
import zhCN from "../../../messages/zh-CN.json";

describe("workspaceDocFreshness", () => {
  it("is fresh pages over all pages, not mean page confidence", () => {
    const repos = [
      { page_count: 7253, fresh_page_count: 6263, doc_coverage_pct: 99.8 },
      { page_count: 0, fresh_page_count: 0, doc_coverage_pct: 0 },
    ];
    expect(workspaceDocFreshness(repos)).toEqual({ fresh: 6263, pages: 7253, pct: 86 });
  });

  it("floors, so a nearly fresh workspace never reads 100%", () => {
    expect(workspaceDocFreshness([{ page_count: 1000, fresh_page_count: 997 }]).pct).toBe(99);
  });

  it("has no figure without pages", () => {
    expect(workspaceDocFreshness([{ page_count: 0, fresh_page_count: 0 }]).pct).toBeNull();
  });

  it("names both counts in every locale", () => {
    for (const [locale, messages] of [["en", en], ["zh-CN", zhCN]] as const) {
      const t = createTranslator({ locale, messages, namespace: "views.workspace.ribbon" });
      const sub = t("docFreshnessSub", { fresh: "6,263", pages: "7,253" });
      expect(sub).toContain("6,263");
      expect(sub).toContain("7,253");
    }
  });
});
