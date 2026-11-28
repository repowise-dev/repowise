import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { HealthLede } from "../../src/overview/health-lede.js";
import { DEFAULT_OWNERSHIP_LABELS } from "../../src/owners/labels.js";

// A viewer whose runtime locale groups digits in lakhs (en-IN: 12,61,599).
// Server-rendered figures must not depend on it, or hydration mismatches.
const toLocaleString = Number.prototype.toLocaleString;
beforeEach(() => {
  Number.prototype.toLocaleString = function (this: number) {
    return toLocaleString.call(this, "en-IN");
  };
});
afterEach(() => {
  Number.prototype.toLocaleString = toLocaleString;
});

describe("figures under an en-IN runtime locale", () => {
  it("HealthLede hands its hint en-US counts", () => {
    let counts: { hotspots: string; files: string } | null = null;
    render(
      <HealthLede
        score={8}
        hotspotHealth={6}
        hotspotCount={105_900}
        fileCount={1_261_599}
        href="/h"
        copy={{
          hint: (c) => {
            counts = c;
            return "";
          },
        }}
      />,
    );
    expect(counts).toEqual({ hotspots: "105,900", files: "1,261,599" });
  });

  it("copy built from counts keeps en-US grouping", () => {
    expect(DEFAULT_OWNERSHIP_LABELS.files(1_261_599)).toBe("1,261,599 files");
  });
});
