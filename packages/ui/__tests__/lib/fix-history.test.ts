import { describe, it, expect } from "vitest";
import { summarizeFixHistory } from "../../src/lib/fix-history.js";

const daysAgo = (n: number) =>
  new Date(Date.now() - n * 86_400_000).toISOString();

describe("summarizeFixHistory", () => {
  it("is silent for a file with no counted fixes", () => {
    expect(summarizeFixHistory(0, daysAgo(3), true)).toBeNull();
    expect(summarizeFixHistory(null)).toBeNull();
    expect(summarizeFixHistory(undefined)).toBeNull();
  });

  it("pairs the count with the age of the last fix", () => {
    const fix = summarizeFixHistory(3, daysAgo(2));
    expect(fix?.countLabel).toBe("3 fixes");
    expect(fix?.age).toBe("2d ago");
    expect(fix?.label).toBe("3 fixes · last 2d ago");
  });

  it("singularizes a lone fix", () => {
    expect(summarizeFixHistory(1, daysAgo(1))?.countLabel).toBe("1 fix");
  });

  it("keeps the count when the timestamp is unknown", () => {
    const fix = summarizeFixHistory(4, null);
    expect(fix?.age).toBeNull();
    expect(fix?.label).toBe("4 fixes");
  });

  it("drops the magnet flag when there is no age to anchor it", () => {
    expect(summarizeFixHistory(4, null, true)?.magnet).toBe(false);
    expect(summarizeFixHistory(4, "not-a-date", true)?.magnet).toBe(false);
  });

  it("drops the magnet flag on a future timestamp rather than reading 'in 3d'", () => {
    const fix = summarizeFixHistory(4, daysAgo(-3), true);
    expect(fix?.age).toBeNull();
    expect(fix?.magnet).toBe(false);
  });

  it("keeps the magnet flag when an age is available", () => {
    expect(summarizeFixHistory(4, daysAgo(5), true)?.magnet).toBe(true);
  });

  it("never invents a magnet the data did not claim", () => {
    expect(summarizeFixHistory(9, daysAgo(1))?.magnet).toBe(false);
    expect(summarizeFixHistory(9, daysAgo(1), false)?.magnet).toBe(false);
  });
});
