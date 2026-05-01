import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { ChurnBar } from "../../src/git/churn-bar.js";

describe("ChurnBar", () => {
  it("renders with red fill at high percentile", () => {
    const { container } = render(<ChurnBar percentile={85} />);
    expect(container.querySelector(".bg-red-500")).not.toBeNull();
  });

  it("clamps width to 0–100%", () => {
    const { container } = render(<ChurnBar percentile={150} />);
    const inner = container.querySelector(".bg-red-500") as HTMLElement;
    expect(inner.style.width).toBe("100%");
  });
});
