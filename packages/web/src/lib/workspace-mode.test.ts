import { describe, expect, it } from "vitest";
import { shouldRedirectFromWorkspace } from "./workspace-mode";

describe("shouldRedirectFromWorkspace", () => {
  it("redirects only when workspace mode is explicitly false", () => {
    expect(shouldRedirectFromWorkspace(false)).toBe(true);
    expect(shouldRedirectFromWorkspace(true)).toBe(false);
    expect(shouldRedirectFromWorkspace(null)).toBe(false);
  });
});
