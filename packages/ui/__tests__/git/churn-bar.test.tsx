import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { ChurnBar } from "../../src/git/churn-bar.js";

describe("ChurnBar", () => {
  it("renders with error fill at high percentile", () => {
    const { container } = render(<ChurnBar percentile={85} />);
    expect(
      container.querySelector(".bg-\\[var\\(--color-error\\)\\]"),
    ).not.toBeNull();
  });

  it("clamps width to 0–100%", () => {
    const { container } = render(<ChurnBar percentile={150} />);
    const inner = container.querySelector(
      ".bg-\\[var\\(--color-error\\)\\]",
    ) as HTMLElement;
    expect(inner.style.width).toBe("100%");
  });
});
