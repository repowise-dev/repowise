import { describe, it, expect } from "vitest";
import { hostedStoryUrl } from "../../src/shared/hostedLinks";

describe("hostedStoryUrl", () => {
  it("opens the story at its moment, tagged with the surface and nothing else", () => {
    expect(hostedStoryUrl("vscode_setup_done", "mcp")).toBe(
      "https://repowise.dev/hosted?src=vscode_setup_done#mcp",
    );
  });
});
