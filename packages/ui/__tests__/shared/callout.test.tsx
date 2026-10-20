import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { Callout } from "../../src/shared/callout";

describe("Callout", () => {
  it("warning keeps a neutral surface with the tone only on dot and word", () => {
    const { container } = render(
      <Callout tone="warning" label="Stale">
        The index is 40 commits behind.
      </Callout>,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(root).toHaveAttribute("role", "status");
    expect(root.className).toMatch(/bg-\[var\(--color-bg-surface\)\]/);
    expect(root.className).not.toMatch(/warning/);
    expect(screen.getByText("Stale")).toBeInTheDocument();
    expect(container.querySelector("[class*='bg-[var(--color-warning)]']")).not.toBeNull();
  });

  it("falls back to a default word per tone", () => {
    render(<Callout tone="warning">Partial results.</Callout>);
    expect(screen.getByText("Warning")).toBeInTheDocument();
  });

  it("error announces as an alert", () => {
    render(<Callout tone="error">Sync failed.</Callout>);
    expect(screen.getByRole("alert")).toHaveTextContent("Sync failed.");
  });

  it("renders the action", () => {
    render(<Callout action={<button type="button">Refresh</button>}>Numbers changed.</Callout>);
    expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
  });
});
