import { render, screen } from "@testing-library/react";
import type { StatsScale } from "@repowise-dev/types/stats";
import { describe, expect, it } from "vitest";
import { SizeClassHero } from "../../src/stats/size-class-hero.js";

function makeScale(overrides: Partial<StatsScale> = {}): StatsScale {
  return {
    file_count: 1_234_567,
    symbol_count: 98_432,
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
  it("names the size class and its blurb", () => {
    render(<SizeClassHero scale={makeScale()} repoName="acme" />);

    expect(screen.getByRole("heading", { name: "Metropolis" })).toBeInTheDocument();
    expect(screen.getByText("A large codebase.")).toBeInTheDocument();
    expect(screen.getByText("acme")).toBeInTheDocument();
  });

  it("renders no figures of its own", () => {
    // The headline counts moved to StatRibbon. Rendering them here too put a
    // boxed number directly above the hairline row carrying the same number,
    // so the hero is now identity-only and this guards against them creeping
    // back in.
    render(<SizeClassHero scale={makeScale()} />);

    expect(screen.queryByText("1.2M")).not.toBeInTheDocument();
    expect(screen.queryByText("98.4K")).not.toBeInTheDocument();
    expect(screen.queryByText(/Lines of code/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Symbols/i)).not.toBeInTheDocument();
  });

  it("omits the eyebrow when no repo name is given", () => {
    const { container } = render(<SizeClassHero scale={makeScale()} />);
    expect(container.querySelector(".tracking-\\[0\\.2em\\]")).toBeNull();
  });
});
