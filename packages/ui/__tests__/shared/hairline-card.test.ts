import { describe, expect, it } from "vitest";

import { hairlineCardClass } from "../../src/shared/hairline-card";

const tokens = (variant?: Parameters<typeof hairlineCardClass>[0], highlighted = false) =>
  hairlineCardClass(variant, { highlighted }).split(/\s+/);

describe("hairlineCardClass", () => {
  it("draws a hairline on the page ground: no fill, no shadow", () => {
    const classes = tokens();
    expect(classes).toContain("border");
    expect(classes).toContain("border-[var(--color-border-default)]");
    expect(classes).toContain("bg-transparent");
    expect(classes.some((c) => c.startsWith("shadow"))).toBe(false);
    expect(classes.some((c) => c.startsWith("hover:"))).toBe(false);
  });

  it("strengthens the hairline on hover and focus within for a card of controls", () => {
    const classes = tokens("group");
    expect(classes).toContain("hover:border-[var(--color-border-hover)]");
    expect(classes).toContain("focus-within:border-[var(--color-border-hover)]");
    expect(classes.some((c) => c.includes("ring-"))).toBe(false);
  });

  it("gives a card that is itself a control the neutral focus ring and no accent", () => {
    const classes = tokens("link");
    expect(classes).toContain("focus-visible:ring-[var(--color-focus-ring)]");
    expect(classes.some((c) => c.includes("accent"))).toBe(false);
  });

  it("holds the strong hairline with a faint accent ring when highlighted", () => {
    const classes = tokens("group", true);
    expect(classes).toContain("border-[var(--color-border-hover)]");
    expect(classes).toContain("ring-[var(--color-accent-primary)]/40");
    expect(classes).not.toContain("border-[var(--color-border-default)]");
  });
});
