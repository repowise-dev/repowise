// @vitest-environment node
/**
 * Every `@repowise-dev/types` subpath resolves under vitest.
 *
 * `vitest.config.ts` hand-mirrors the types package's `exports` map, because
 * the alias order matters and a bare entry would shadow the subpaths. A
 * hand-mirrored list drifts, and it drifts silently: a missing alias only
 * breaks the day a test transitively *value*-imports that subpath, since
 * type-only imports are erased before the resolver ever sees them.
 *
 * This is the notice. Adding a subpath to the types package and not to the
 * alias map fails here rather than in whichever unrelated suite imports it
 * next.
 */
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import manifest from "../../types/package.json" with { type: "json" };
import config from "../vitest.config";

const here = dirname(fileURLToPath(import.meta.url));

function aliasMap(): Record<string, string> {
  return (config.resolve?.alias ?? {}) as Record<string, string>;
}

describe("types subpath aliases", () => {
  it("covers every subpath the types package publishes", () => {
    const declared = Object.keys(manifest.exports as Record<string, string>)
      .filter((key) => key !== "." && !key.includes("*"))
      .map((key) => `@repowise-dev/types${key.slice(1)}`);
    const aliased = new Set(Object.keys(aliasMap()));
    expect(declared.filter((name) => !aliased.has(name))).toEqual([]);
  });

  it("points every alias at a file that exists", () => {
    const missing = Object.entries(aliasMap()).filter(
      ([, target]) => !existsSync(resolve(here, target)),
    );
    expect(missing).toEqual([]);
  });

  it("keeps the bare entry last so it cannot shadow a subpath", () => {
    const keys = Object.keys(aliasMap());
    expect(keys.indexOf("@repowise-dev/types")).toBe(keys.length - 1);
  });
});
