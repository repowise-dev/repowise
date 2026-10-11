import { describe, it, expect } from "vitest";
import { toFriendlyMessage, DEFAULT_ERROR_MESSAGE } from "../../src/lib/errors.js";

describe("toFriendlyMessage", () => {
  it("keeps a human-written server message", () => {
    expect(toFriendlyMessage(new Error("Path does not exist or is not a git repository"))).toBe(
      "Path does not exist or is not a git repository",
    );
  });

  it("translates network failures", () => {
    expect(toFriendlyMessage(new TypeError("Failed to fetch"))).toMatch(/reach the server/i);
    expect(toFriendlyMessage(new Error("NetworkError when attempting to fetch resource."))).toMatch(
      /reach the server/i,
    );
  });

  it("translates aborted requests", () => {
    expect(toFriendlyMessage(new DOMException("The operation was aborted.", "AbortError"))).toBe(
      "The request was cancelled.",
    );
  });

  it("strips a leaked error-class prefix", () => {
    expect(toFriendlyMessage(new Error("TypeError: x is not a function"))).toBe(
      "x is not a function",
    );
  });

  it("falls back for empty and object-ish errors", () => {
    expect(toFriendlyMessage(new Error(""))).toBe(DEFAULT_ERROR_MESSAGE);
    expect(toFriendlyMessage({})).toBe(DEFAULT_ERROR_MESSAGE);
    expect(toFriendlyMessage("[object Object]")).toBe(DEFAULT_ERROR_MESSAGE);
    expect(toFriendlyMessage(null, "Custom fallback")).toBe("Custom fallback");
  });
});
