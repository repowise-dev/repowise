import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { CleanState } from "../../src/shared/clean-state";

describe("CleanState", () => {
  it("is the positive tone, named with its scope, and unboxed", () => {
    const { container } = render(
      <CleanState title="No dead code across 5,612 files" description="No unreachable files." />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root).toHaveAttribute("data-tone", "positive");
    expect(root.className).not.toMatch(/border-\[var/);
    expect(screen.getByRole("heading", { name: "No dead code across 5,612 files" })).toBeInTheDocument();
    expect(screen.getByText("No unreachable files.")).toBeInTheDocument();
  });

  it("aligns to the start when it sits under a lede", () => {
    const { container } = render(<CleanState title="All clear" align="start" />);
    expect((container.firstElementChild as HTMLElement).className).toMatch(/items-start/);
  });
});
