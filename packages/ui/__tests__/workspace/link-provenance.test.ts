import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { contractOriginCopy, linkStrategyCopy } from "../../src/workspace/link-provenance.js";

// Read the closed vocabularies from core itself, so a value added there without
// a label here fails this test rather than rendering as a humanised token.
const ORIGINS_PY = readFileSync(
  join(__dirname, "../../../core/src/repowise/core/workspace/origins.py"),
  "utf-8",
).replace(/\r/g, "");

function enumValues(className: string): string[] {
  const body = ORIGINS_PY.split(`class ${className}(StrEnum):`)[1]?.split(/\n\S/)[0] ?? "";
  return [...body.matchAll(/^\s+[A-Z_]+ = "([a-z_]+)"$/gm)].map((m) => m[1]!);
}

describe("link strategy copy", () => {
  const strategies = enumValues("LinkStrategy");

  it("reads every strategy core declares", () => {
    expect(strategies).toContain("exact_id");
    expect(strategies).toContain("gateway_route");
    expect(strategies.length).toBeGreaterThanOrEqual(12);
  });

  it.each(strategies)("labels %s in its own words", (s) => {
    const copy = linkStrategyCopy(s)!;
    expect(copy.label).toBeTruthy();
    expect(copy.meaning).not.toMatch(/does not describe/);
  });

  it("names the matches the drawers lead with", () => {
    expect(linkStrategyCopy("unique_path")?.label).toBe("Matched by path only");
    expect(linkStrategyCopy("gateway_route")?.label).toBe("Routed through a gateway");
    expect(linkStrategyCopy("host_only")?.label).toBe("Host only");
  });

  it("humanises a strategy this build predates and never goes blank", () => {
    expect(linkStrategyCopy("mesh_routed")).toEqual({
      label: "Mesh routed",
      meaning: "This version does not describe this matching rule.",
    });
    expect(linkStrategyCopy(null)).toBeNull();
    expect(linkStrategyCopy("")).toBeNull();
  });
});

describe("contract origin copy", () => {
  const origins = enumValues("ContractOrigin");

  it("reads every origin core declares", () => {
    expect(origins).toContain("client_call");
    expect(origins).toContain("dataflow");
    expect(origins.length).toBeGreaterThanOrEqual(12);
  });

  it.each(origins)("labels %s in its own words", (o) => {
    const copy = contractOriginCopy(o)!;
    expect(copy.label).toBeTruthy();
    expect(copy.meaning).not.toMatch(/does not describe/);
  });

  it("humanises an origin this build predates", () => {
    expect(contractOriginCopy("build_script")?.label).toBe("Build script");
    expect(contractOriginCopy(undefined)).toBeNull();
  });
});
