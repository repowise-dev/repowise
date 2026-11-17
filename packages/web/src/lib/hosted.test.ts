import { describe, expect, it } from "vitest";
import { homeLink, hostedLink, pickNudge } from "./hosted";

const signedOut = { signed_in: false, hints_enabled: true };

describe("hostedLink", () => {
  it("carries the surface and the moment, nothing else", () => {
    expect(hostedLink("mcp", "mcp")).toBe("https://repowise.dev/hosted?src=local_web_mcp#mcp");
  });

  it("opens the top of the hosted page without a moment", () => {
    expect(hostedLink("setup_card")).toBe("https://repowise.dev/hosted?src=local_web_setup_card");
  });
});

describe("homeLink", () => {
  it("opens the homepage with only the surface", () => {
    expect(homeLink("setup_card")).toBe("https://repowise.dev/?src=local_web_setup_card");
  });
});

describe("pickNudge", () => {
  it("shows only the first tip that applies", () => {
    expect(pickNudge([false, "docs", "mcp"], signedOut, true)).toBe("docs");
    expect(pickNudge([null, undefined, false], signedOut, true)).toBeNull();
  });

  it("shows nothing for a signed-in user", () => {
    expect(pickNudge(["mcp"], { ...signedOut, signed_in: true }, true)).toBeNull();
  });

  it("shows nothing when the CLI's hints switch is off", () => {
    expect(pickNudge(["mcp"], { ...signedOut, hints_enabled: false }, true)).toBeNull();
  });

  it("shows nothing when the local tips switch is off", () => {
    expect(pickNudge(["mcp"], signedOut, false)).toBeNull();
  });

  it("shows nothing while identity is unknown", () => {
    expect(pickNudge(["mcp"], null, true)).toBeNull();
  });
});
