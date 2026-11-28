import { describe, expect, it } from "vitest";

import { bulkAgentPromptLabel } from "../../src/health/ai-prompt-action";

describe("bulkAgentPromptLabel", () => {
  it("names one item as this one, not these 1", () => {
    expect(bulkAgentPromptLabel(1)).toBe("Copy prompt for this one");
  });

  it("counts a group", () => {
    expect(bulkAgentPromptLabel(4)).toBe("Copy prompt for these 4");
  });
});
