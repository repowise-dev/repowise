// The export map is the published contract. `./hosted` in particular is
// consumed only from outside this repo, so nothing else in here would fail if
// a packaging change dropped it.
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import manifest from "../package.json" with { type: "json" };

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const exportMap = manifest.exports as Record<string, string>;

describe("published export map", () => {
  it("keeps the hosted provider reachable", () => {
    expect(exportMap["./hosted"]).toBe("./src/hosted.ts");
  });

  it("points every subpath at a file that exists", () => {
    const missing = Object.entries(exportMap).filter(
      ([, target]) => !existsSync(join(packageRoot, target)),
    );
    expect(missing).toEqual([]);
  });

  it("is published under the scope the workflow authenticates against", () => {
    expect(manifest.name).toBe("@repowise-dev/api-client");
    expect(manifest.publishConfig?.registry).toBe("https://npm.pkg.github.com");
  });
});
