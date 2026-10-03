import { describe, expect, it } from "vitest";
import { hostedLink, pickNudge } from "./hosted";

const signedOut = { anon_id: "a1", signed_in: false, hints_enabled: true };

describe("hostedLink", () => {
  it("carries the surface, the anonymous id and the moment", () => {
    expect(hostedLink("mcp", "mcp", "a1")).toBe(
      "https://repowise.dev/hosted?src=local_web_mcp&aid=a1#mcp",
    );
  });

  it("leaves aid out when there is no anonymous id", () => {
    expect(hostedLink("share", "link", null)).toBe(
      "https://repowise.dev/hosted?src=local_web_share#link",
    );
  });
});

describe("pickNudge", () => {
  it("shows only the first tip that applies", () => {
    expect(pickNudge([false, "docs", "share"], signedOut, true)).toBe("docs");
    expect(pickNudge([null, undefined, false], signedOut, true)).toBeNull();
  });

  it("shows nothing for a signed-in user", () => {
    expect(pickNudge(["share"], { ...signedOut, signed_in: true }, true)).toBeNull();
  });

  it("shows nothing when the CLI's hints switch is off", () => {
    expect(pickNudge(["share"], { ...signedOut, hints_enabled: false }, true)).toBeNull();
  });

  it("shows nothing when the local tips switch is off", () => {
    expect(pickNudge(["share"], signedOut, false)).toBeNull();
  });

  it("shows nothing while identity is unknown", () => {
    expect(pickNudge(["share"], null, true)).toBeNull();
  });
});
