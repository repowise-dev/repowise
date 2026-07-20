import { render, screen } from "@testing-library/react";
import type { StatsScale } from "@repowise-dev/types/stats";
import { describe, expect, it } from "vitest";
import { SizeClassHero } from "../../src/stats/size-class-hero.js";

function makeScale(overrides: Partial<StatsScale> = {}): StatsScale {
  return {
    file_count: 1_234_567,
    symbol_count: 98_432,
    entry_point_count: 12,
    module_count: 80,
    total_nloc: 4_200_000,
    language_count: 3,
    languages: [],
    size_class: {
      name: "Metropolis",
      blurb: "A large codebase.",
      nloc: 4_200_000,
    },
    ...overrides,
  };
}

describe("SizeClassHero", () => {
  it("renders large file and symbol counts compactly", () => {
    render(<SizeClassHero scale={makeScale()} />);

    expect(screen.getByText("1.2M")).toBeInTheDocument();
    expect(screen.getByText("98.4K")).toBeInTheDocument();
    expect(screen.queryByText("1,234,567")).not.toBeInTheDocument();
    expect(screen.queryByText("98,432")).not.toBeInTheDocument();
  });

  it("renders small counts verbatim", () => {
    render(<SizeClassHero scale={makeScale({ file_count: 999, symbol_count: 42 })} />);

    expect(screen.getByText("999")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("never clips a figure to an ellipsis", () => {
    render(<SizeClassHero scale={makeScale({ total_nloc: 485_300 })} />);

    const value = screen.getByText("485.3K");
    expect(value).toHaveClass("whitespace-nowrap");
    expect(value).not.toHaveClass("truncate");
    // The tile must not be allowed to shrink below its figure.
    expect(value.parentElement).not.toHaveClass("min-w-0");
  });
});
