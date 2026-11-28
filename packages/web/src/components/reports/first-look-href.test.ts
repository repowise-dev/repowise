import { existsSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { FirstLookRouteKey } from "@repowise-dev/types/first-look";
import { firstLookHref } from "./first-look-href";

const P = "/repos/r1";

describe("firstLookHref", () => {
  it("maps every route key to a page that exists", () => {
    const cases: [FirstLookRouteKey, Record<string, string | number>, string][] = [
      ["file", { path: "src/a b.py", line: 12 }, `${P}/files/src/a%20b.py?line=12`],
      ["file", { path: "src/a.py" }, `${P}/files/src/a.py`],
      ["security", {}, `${P}/security`],
      ["dead_code", {}, `${P}/code-health?tab=dead-code`],
      ["health_file", { path: "src/a.py" }, `${P}/code-health?file=src%2Fa.py`],
      ["docs_drift", {}, `${P}/docs/drift`],
      ["decision", { id: "d1" }, `${P}/decisions/d1`],
      ["ownership", {}, `${P}/ownership`],
    ];
    for (const [route, params, href] of cases) expect(firstLookHref(P, route, params)).toBe(href);

    const pages = ["files/[...path]", "security", "code-health", "docs/drift", "decisions/[decisionId]", "ownership"];
    for (const page of pages) {
      expect(existsSync(join(__dirname, "../../app/repos/[id]", page, "page.tsx")), page).toBe(true);
    }
  });

  it("has no link for a file route without a path", () => {
    expect(firstLookHref(P, "file", {})).toBeNull();
  });
});
