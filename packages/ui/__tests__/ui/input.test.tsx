import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Input } from "../../src/ui/input.js";

describe("Input", () => {
  it("is a recessed field that lifts to the surface on focus", () => {
    render(<Input aria-label="Name" />);
    const field = screen.getByRole("textbox", { name: "Name" });
    expect(field.className).toContain("bg-[var(--color-field-bg)]");
    expect(field.className).toContain("border-[var(--color-field-border)]");
    expect(field.className).toContain("hover:border-[var(--color-field-border-hover)]");
    expect(field.className).toContain("focus-visible:bg-[var(--color-bg-surface)]");
    expect(field.className).toContain("focus-visible:ring-[var(--color-focus-ring)]");
  });

  it("dims a disabled value's text, not the whole box", () => {
    render(<Input aria-label="Branch" disabled defaultValue="main" />);
    const field = screen.getByRole("textbox", { name: "Branch" });
    expect(field.className).not.toContain("disabled:opacity-50");
    expect(field.className).toContain("disabled:text-[var(--color-text-secondary)]");
  });
});
