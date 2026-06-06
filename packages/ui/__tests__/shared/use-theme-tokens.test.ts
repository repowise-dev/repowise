import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  getCommunityFamily,
  COMMUNITY_FAMILY_COUNT,
} from "../../src/shared/use-theme-tokens";

// Stamp the 12 community token pairs onto <html> so getComputedStyle resolves
// them (mirrors what globals.css does at runtime). Distinct sentinel values
// per family let us assert the cyclic mapping precisely.
function stampCommunityTokens() {
  const root = document.documentElement;
  for (let n = 1; n <= COMMUNITY_FAMILY_COUNT; n++) {
    root.style.setProperty(`--color-community-${n}`, `#0000${(n).toString(16).padStart(2, "0")}`);
    root.style.setProperty(`--color-community-${n}-soft`, `#1100${(n).toString(16).padStart(2, "0")}`);
  }
}

describe("getCommunityFamily", () => {
  beforeEach(() => stampCommunityTokens());
  afterEach(() => {
    const root = document.documentElement;
    for (let n = 1; n <= COMMUNITY_FAMILY_COUNT; n++) {
      root.style.removeProperty(`--color-community-${n}`);
      root.style.removeProperty(`--color-community-${n}-soft`);
    }
  });

  it("returns a hub and satellite from the matching token pair", () => {
    expect(getCommunityFamily(0)).toEqual({ hub: "#000001", satellite: "#110001" });
    expect(getCommunityFamily(2)).toEqual({ hub: "#000003", satellite: "#110003" });
  });

  it("cycles community ids modulo the family count", () => {
    expect(getCommunityFamily(COMMUNITY_FAMILY_COUNT)).toEqual(getCommunityFamily(0));
    expect(getCommunityFamily(COMMUNITY_FAMILY_COUNT + 1)).toEqual(getCommunityFamily(1));
  });

  it("handles negative ids without going out of range", () => {
    expect(getCommunityFamily(-1)).toEqual(getCommunityFamily(COMMUNITY_FAMILY_COUNT - 1));
  });

  it("falls back the satellite to the hub when the soft token is empty", () => {
    document.documentElement.style.removeProperty("--color-community-1-soft");
    const fam = getCommunityFamily(0);
    expect(fam.satellite).toBe(fam.hub);
  });
});
