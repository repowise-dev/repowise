import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { PageFrame, PageShell } from "../../src/shared/page-shell";

describe("PageShell", () => {
  it("wraps the title icon in one neutral tint at 22px", () => {
    render(<PageShell title="Commits" icon={<svg data-testid="icon" />} />);
    const slot = screen.getByTestId("icon").parentElement as HTMLElement;
    expect(slot.className).toMatch(/color-text-tertiary/);
    expect(screen.getByRole("heading", { level: 1 })).toHaveClass("text-[22px]");
  });

  it("supports the narrow width", () => {
    const { container } = render(<PageShell title="Settings" maxWidth="narrow" />);
    expect(container.firstElementChild).toHaveClass("max-w-3xl");
  });
});

describe("PageFrame", () => {
  it("is the same container without a header", () => {
    const { container } = render(
      <PageFrame maxWidth="wide">
        <p>body</p>
      </PageFrame>,
    );
    expect(container.firstElementChild).toHaveClass("max-w-[1600px]");
    expect(container.querySelector("header")).toBeNull();
  });
});
