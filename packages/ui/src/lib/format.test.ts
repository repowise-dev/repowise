import { describe, expect, it } from "vitest";

import { formatTopPercentile, ordinal } from "./format";

describe("formatTopPercentile", () => {
  it.each([
    [100, "top <0.1%"],
    [99.9, "top 0.1%"],
    [98.9, "top 1.1%"],
    [90, "top 10%"],
    [0, "top 100%"],
  ])("renders %s honestly", (percentile, expected) => {
    expect(formatTopPercentile(percentile)).toBe(expected);
    expect(expected).not.toBe("top 0%");
  });

  it("bounds values outside the public 0-100 scale", () => {
    expect(formatTopPercentile(101)).toBe("top <0.1%");
    expect(formatTopPercentile(-1)).toBe("top 100%");
  });
});

describe("ordinal", () => {
  it.each([
    [1, "1st"],
    [2, "2nd"],
    [3, "3rd"],
    [4, "4th"],
    [11, "11th"],
    [12, "12th"],
    [13, "13th"],
    [21, "21st"],
    [93, "93rd"],
    [111, "111th"],
    [0, "0th"],
  ])("renders %s as %s", (n, expected) => {
    expect(ordinal(n)).toBe(expected);
  });
});
