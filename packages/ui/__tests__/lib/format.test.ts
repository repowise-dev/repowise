import { describe, expect, it } from "vitest";
import {
  disambiguateBasenames,
  formatAgeDays,
  formatBytes,
  formatCompact,
  formatDate,
  formatDateTime,
  formatPercent,
  parseDate,
  truncatePath,
} from "../../src/lib/format";

describe("disambiguateBasenames", () => {
  it("keeps the bare basename when nothing collides", () => {
    const labels = disambiguateBasenames(["src/parser.rs", "src/lexer.rs"]);
    expect(labels.get("src/parser.rs")).toBe("parser.rs");
    expect(labels.get("src/lexer.rs")).toBe("lexer.rs");
  });

  it("adds the parent directory to separate a collision", () => {
    const labels = disambiguateBasenames(["src/css/mod.rs", "src/text/mod.rs"]);
    expect(labels.get("src/css/mod.rs")).toBe("css/mod.rs");
    expect(labels.get("src/text/mod.rs")).toBe("text/mod.rs");
  });

  it("walks past one parent when the parent collides too", () => {
    // The case the previous single-level logic got wrong: both are `a/mod.rs`.
    const labels = disambiguateBasenames(["x/a/mod.rs", "y/a/mod.rs"]);
    expect(labels.get("x/a/mod.rs")).toBe("x/a/mod.rs");
    expect(labels.get("y/a/mod.rs")).toBe("y/a/mod.rs");
  });

  it("lengthens only the paths still tied, not the whole set", () => {
    const labels = disambiguateBasenames(["x/a/mod.rs", "y/a/mod.rs", "src/lexer.rs"]);
    expect(labels.get("src/lexer.rs")).toBe("lexer.rs");
    expect(labels.get("x/a/mod.rs")).toBe("x/a/mod.rs");
  });

  it("handles a bare filename with no directory", () => {
    const labels = disambiguateBasenames(["README.md", "docs/README.md"]);
    expect(labels.get("README.md")).toBe("README.md");
    expect(labels.get("docs/README.md")).toBe("docs/README.md");
  });

  it("deduplicates repeated paths and tolerates an empty set", () => {
    expect(disambiguateBasenames([]).size).toBe(0);
    const labels = disambiguateBasenames(["src/mod.rs", "src/mod.rs"]);
    expect(labels.size).toBe(1);
    expect(labels.get("src/mod.rs")).toBe("mod.rs");
  });
});

describe("truncatePath", () => {
  it("leaves a path that fits unchanged", () => {
    expect(truncatePath("src/lib/format.ts", 60)).toBe("src/lib/format.ts");
  });

  // Note: this returns the FEWEST trailing segments that fit, not the most, so
  // it does not match its own docstring example. Pinning actual behaviour here;
  // changing it would move 26 call sites and belongs in its own change.
  it("drops leading segments until the path fits", () => {
    expect(truncatePath("src/very/long/path/file.py", 22)).toBe("…/path/file.py");
  });

  it("falls back to the filename alone when no segment pair fits", () => {
    expect(truncatePath("src/very/long/path/file.py", 12)).toBe("…/file.py");
  });

  it("truncates from the left when even the filename overflows", () => {
    expect(truncatePath("src/a-very-long-filename.py", 10)).toBe("…lename.py");
  });
});

describe("formatCompact", () => {
  it("leaves counts below one thousand unchanged", () => {
    expect(formatCompact(0)).toBe("0");
    expect(formatCompact(999)).toBe("999");
  });

  it("shortens thousands and millions without unnecessary decimal zeroes", () => {
    expect(formatCompact(1_000)).toBe("1K");
    expect(formatCompact(98_432)).toBe("98.4K");
    expect(formatCompact(999_999)).toBe("1M");
    expect(formatCompact(1_234_567)).toBe("1.2M");
  });
});

describe("formatAgeDays", () => {
  it("formats ages shorter than a month in days", () => {
    expect(formatAgeDays(0.5)).toBe("< 1 day");
    expect(formatAgeDays(1)).toBe("1 day");
    expect(formatAgeDays(18)).toBe("18 days");
  });

  it("splits sub-year ages into months and days", () => {
    expect(formatAgeDays(45)).toBe("1 month 15 days");
  });

  it("splits longer ages into years and months", () => {
    expect(formatAgeDays(365)).toBe("1 year");
    expect(formatAgeDays(400)).toBe("1 year 1 month");
    expect(formatAgeDays(725)).toBe("2 years");
    expect(formatAgeDays(730)).toBe("2 years");
    expect(formatAgeDays(1_000)).toBe("2 years 9 months");
  });
});

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

describe("parseDate", () => {
  it("treats a bare ISO string (no tz suffix) as UTC", () => {
    // "2026-08-21T13:23:33" must mean 13:23 UTC, not local time.
    expect(parseDate("2026-08-21T13:23:33").toISOString()).toBe(
      "2026-08-21T13:23:33.000Z",
    );
  });

  it("keeps an explicit Z or offset as-is", () => {
    expect(parseDate("2026-08-21T13:23:33Z").toISOString()).toBe(
      "2026-08-21T13:23:33.000Z",
    );
    expect(parseDate("2026-08-21T21:23:33+08:00").toISOString()).toBe(
      "2026-08-21T13:23:33.000Z",
    );
  });

  it("passes Date instances through unchanged", () => {
    const d = new Date("2026-08-21T13:23:33Z");
    expect(parseDate(d)).toBe(d);
  });
});

describe("formatDate / formatDateTime render UTC, not the local zone", () => {
  it("renders the bare API string as the same wall-clock in UTC", () => {
    // With a plain `new Date("2026-08-21T13:23:33")`, a UTC+8 browser would
    // render 21:23 on the 21st as the local reading of 13:23 UTC. Parsing as
    // UTC and pinning timeZone: "UTC" keeps the shown wall-clock fixed at the
    // value the API returned.
    expect(formatDate("2026-08-21T13:23:33")).toBe("Aug 21, 2026");
    expect(formatDateTime("2026-08-21T13:23:33")).toBe("Aug 21, 2026, 1:23 PM");
  });

  it("renders an explicit Z timestamp at the same UTC wall-clock", () => {
    expect(formatDate("2026-08-21T13:23:33Z")).toBe("Aug 21, 2026");
    expect(formatDateTime("2026-08-21T13:23:33Z")).toBe("Aug 21, 2026, 1:23 PM");
  });
});
