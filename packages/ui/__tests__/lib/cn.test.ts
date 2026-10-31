import { describe, expect, it } from "vitest";
import { cn } from "../../src/lib/cn";

describe("cn", () => {
  it("keeps the caption size beside an arbitrary text colour", () => {
    expect(cn("text-caption", "text-[var(--color-text-tertiary)]")).toBe(
      "text-caption text-[var(--color-text-tertiary)]",
    );
  });

  it("still lets a later size replace the caption size", () => {
    expect(cn("text-caption", "text-xs")).toBe("text-xs");
  });
});
