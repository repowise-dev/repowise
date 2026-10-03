import { describe, it, expect } from "vitest";
import { hostedStoryUrl, readCliAnonId } from "../../src/shared/hostedLinks";

const STATE = { anon_id: "abc123def456" };

describe("hostedStoryUrl", () => {
  it("opens the story at its moment, tagged with the surface", () => {
    expect(hostedStoryUrl("vscode_setup_done", "mcp", "abc123def456")).toBe(
      "https://repowise.dev/hosted?src=vscode_setup_done&aid=abc123def456#mcp",
    );
  });

  it("carries no aid without one", () => {
    expect(hostedStoryUrl("vscode_setup_done", "mcp", null)).toBe(
      "https://repowise.dev/hosted?src=vscode_setup_done#mcp",
    );
  });
});

describe("readCliAnonId", () => {
  it("reads the CLI's install id", () => {
    expect(readCliAnonId(STATE, {})).toBe("abc123def456");
  });

  it.each(["DO_NOT_TRACK", "REPOWISE_TELEMETRY_DISABLED"])(
    "is null when %s is set",
    (name) => {
      expect(readCliAnonId(STATE, { [name]: "1" })).toBeNull();
    },
  );

  it("is null when the CLI's telemetry was turned off", () => {
    expect(readCliAnonId({ ...STATE, telemetry_enabled: false }, {})).toBeNull();
  });

  it("never sends something the site would reject", () => {
    expect(readCliAnonId({ anon_id: "Not An Id!" }, {})).toBeNull();
    expect(readCliAnonId(null, {})).toBeNull();
    expect(readCliAnonId({}, {})).toBeNull();
  });
});
