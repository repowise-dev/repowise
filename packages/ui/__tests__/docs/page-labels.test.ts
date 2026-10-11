import { describe, it, expect } from "vitest";
import { displayLabel, treeLabel } from "../../src/docs/page-labels.js";
import type { DocPageSummary } from "@repowise-dev/types/docs";

function summary(overrides: Partial<DocPageSummary> = {}): DocPageSummary {
  return {
    id: "p1",
    repository_id: "r1",
    page_type: "file_page",
    title: "Page",
    target_path: "src/foo.ts",
    source_hash: "h",
    model_name: "m",
    provider_name: "p",
    input_tokens: 0,
    output_tokens: 0,
    cached_tokens: 0,
    generation_level: 0,
    version: 1,
    confidence: 1,
    freshness_status: "fresh",
    human_notes: null,
    created_at: "",
    updated_at: "",
    ...overrides,
  };
}

const CYCLE_BODY = [
  "# Circular Dependency: scc-103",
  "",
  "- `packages/core/src/generation/page_generator/core.py`",
  "- `packages/core/src/generation/page_generator/prompts.py`",
].join("\n");

describe("displayLabel", () => {
  it("names a cycle after the directory its members share", () => {
    const page = summary({
      page_type: "scc_page",
      title: "Circular Dependency: scc-103",
      target_path: "scc-103",
      content: CYCLE_BODY,
    });
    expect(displayLabel(page)).toBe("Cycle: generation/page_generator");
  });

  it("keeps the title when the row arrived without its body", () => {
    // A summary listing carries no bodies, so there is nothing to derive from.
    // The title is a worse label, but it is a true one.
    const page = summary({
      page_type: "scc_page",
      title: "Circular Dependency: scc-103",
      target_path: "scc-103",
    });
    expect(displayLabel(page)).toBe("Circular Dependency: scc-103");
  });

  it("names other page types without touching the body at all", () => {
    expect(
      displayLabel(summary({ page_type: "layer_page", title: "Layer: Runtime" })),
    ).toBe("Runtime");
    expect(
      displayLabel(
        summary({
          page_type: "module_page",
          title: "Module: ingestion",
          target_path: "src/ingestion",
        }),
      ),
    ).toBe("ingestion");
  });
});

describe("treeLabel", () => {
  it("names a file relative to the module it hangs off", () => {
    const parent = summary({ page_type: "module_page", target_path: "src/api" });
    const page = summary({ target_path: "src/api/routes/users.ts" });
    expect(treeLabel(page, parent)).toBe("routes/users.ts");
  });
});
