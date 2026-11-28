import { describe, expect, it } from "vitest";

import { buildPerformanceAreaPrompt } from "../../src/health/ai-prompts/performance-area-prompt";
import { opportunity } from "./fixtures/performance";

const PAGED_NOTE = "later pages may hold more in this area";

describe("buildPerformanceAreaPrompt", () => {
  it("says the area's causes come from one page when the queue runs past it", () => {
    const opportunities = [opportunity({ opportunity_id: "a", file_path: "pkg/a/x.py" })];
    expect(buildPerformanceAreaPrompt({ area: "pkg/a", opportunities, paged: true })).toContain(PAGED_NOTE);
    expect(buildPerformanceAreaPrompt({ area: "pkg/a", opportunities })).not.toContain(PAGED_NOTE);
  });
});
