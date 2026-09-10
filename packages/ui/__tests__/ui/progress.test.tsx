import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { Progress } from "../../src/ui/progress.js";

afterEach(() => vi.restoreAllMocks());

describe("Progress", () => {
  it.each([
    { value: 4820, expected: 100 },
    { value: -20, expected: 0 },
    { value: 48, expected: 48 },
    { value: 0, expected: 0 },
    { value: 100, expected: 100 },
  ])("uses $expected for the bar and accessibility when value is $value", ({ value, expected }) => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<Progress value={value} />);

    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", String(expected));
    expect(bar).toHaveAttribute("aria-valuemax", "100");
    expect(bar).toHaveAttribute("data-state", expected === 100 ? "complete" : "loading");
    expect(bar.firstElementChild).toHaveStyle({ transform: `translateX(${expected - 100}%)` });
    expect(error).not.toHaveBeenCalled();
  });

  it.each([undefined, NaN, Infinity, -Infinity])("is indeterminate for %s", (value) => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<Progress {...(value === undefined ? {} : { value })} />);

    const bar = screen.getByRole("progressbar");
    expect(bar).not.toHaveAttribute("aria-valuenow");
    expect(bar).toHaveAttribute("data-state", "indeterminate");
    expect(bar.firstElementChild).toHaveStyle({ transform: "translateX(-100%)" });
    expect(error).not.toHaveBeenCalled();
  });
});
