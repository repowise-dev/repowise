import { describe, expect, it } from "vitest";
import { formatBytes, formatPercent } from "../../src/lib/format";

describe("formatPercent", () => {
  it("rounds ratios to whole percentages by default", () => {
    expect(formatPercent(0.873)).toBe("87%");
    expect(formatPercent(1)).toBe("100%");
    expect(formatPercent(0)).toBe("0%");
  });

  it("supports fixed decimal places", () => {
    expect(formatPercent(0.8734, 1)).toBe("87.3%");
  });

  it("returns an em dash for non-finite input", () => {
    expect(formatPercent(Number.NaN)).toBe("—");
    expect(formatPercent(Number.POSITIVE_INFINITY)).toBe("—");
  });
});

describe("formatBytes", () => {
  it("formats common storage sizes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(1_048_576)).toBe("1.0 MB");
    expect(formatBytes(1_073_741_824)).toBe("1.0 GB");
  });

  it("uses whole numbers for values >= 10 in a unit", () => {
    expect(formatBytes(10 * 1024)).toBe("10 KB");
  });

  it("returns an em dash for invalid input", () => {
    expect(formatBytes(-1)).toBe("—");
    expect(formatBytes(Number.NaN)).toBe("—");
  });
});
