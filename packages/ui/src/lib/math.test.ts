import { describe, expect, it } from "vitest";

import { median } from "./math";

describe("median", () => {
  it("averages the two middle values of an even count", () => {
    expect(median([1, 2, 3, 10])).toBe(2.5);
    expect(median([4, 8])).toBe(6);
  });

  it("takes the middle value of an odd count", () => {
    expect(median([9, 1, 5])).toBe(5);
  });

  it("sorts numerically and leaves the input alone", () => {
    const values = [10, 9, 100, 1];
    expect(median(values)).toBe(9.5);
    expect(values).toEqual([10, 9, 100, 1]);
  });

  it("is 0 for an empty list", () => {
    expect(median([])).toBe(0);
  });
});
