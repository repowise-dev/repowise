import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LowerPriorityTag } from "../../src/health/lower-priority-tag";
import { PerformanceView } from "../../src/health/performance-view";
import { adapter, opportunity, page } from "./fixtures/performance";

describe("LowerPriorityTag", () => {
  it("shows the reason once, without doubling the prefix", () => {
    render(<LowerPriorityTag reason="lower priority: long, but its control flow is simple" />);
    expect(screen.getByText(/Lower priority: long, but its control flow is simple/)).toBeTruthy();
  });

  it("shows a bare reason under the prefix", () => {
    render(<LowerPriorityTag reason="near the bar" />);
    expect(screen.getByText(/Lower priority: near the bar/)).toBeTruthy();
  });

  it.each([undefined, null, "", "   "])("renders nothing for %p", (reason) => {
    const { container } = render(<LowerPriorityTag reason={reason} />);
    expect(container.firstChild).toBeNull();
  });
});

describe("performance queue row", () => {
  it("labels a demoted opportunity and leaves the rest unlabelled", async () => {
    const demoted = opportunity({ lower_priority: "lower priority: where this code runs is unknown" });
    render(<PerformanceView adapter={adapter({ getPerformanceOpportunities: async () => page({ items: [demoted] }) })} />);
    expect(await screen.findByText(/where this code runs is unknown/)).toBeTruthy();
  });

  it("renders no label when the server does not send one", async () => {
    render(<PerformanceView adapter={adapter()} />);
    await screen.findAllByRole("listitem");
    expect(document.querySelector("[data-lower-priority]")).toBeNull();
  });
});
