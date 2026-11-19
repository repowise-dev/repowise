import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TimeZoneField, timeZoneChoices } from "../../src/settings/time-zone-field.js";

function open() {
  const onChange = vi.fn();
  render(
    <TimeZoneField value="auto" detected="Europe/Berlin" onChange={onChange} ariaLabel="Time zone" />,
  );
  const input = screen.getByRole("combobox", { name: "Time zone" });
  fireEvent.focus(input);
  return { input, onChange };
}

describe("TimeZoneField", () => {
  it("leads with Auto, naming the detected zone, then UTC", () => {
    open();
    const options = screen.getAllByRole("option");
    expect(options[0]).toHaveTextContent("auto");
    expect(options[0]).toHaveTextContent("This browser, Europe/Berlin");
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    expect(options[1]).toHaveTextContent("UTC");
  });

  it("searches zones by name and reports the pick", () => {
    const { input, onChange } = open();
    fireEvent.change(input, { target: { value: "tokyo" } });
    const options = screen.getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["Asia/Tokyo"]);
    fireEvent.mouseDown(options[0]!);
    expect(onChange).toHaveBeenCalledWith("Asia/Tokyo");
  });

  it("never offers typed text that is not a listed zone", () => {
    const { input, onChange } = open();
    fireEvent.change(input, { target: { value: "Mars/Base" } });
    expect(screen.queryAllByRole("option")).toEqual([]);
    expect(screen.getByText("No time zone matches.")).toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("reads Auto with the detected zone when closed, and a picked zone by name", () => {
    const { rerender } = render(
      <TimeZoneField value="auto" detected="Asia/Kolkata" onChange={vi.fn()} ariaLabel="Time zone" />,
    );
    expect(screen.getByRole("combobox", { name: "Time zone" })).toHaveValue("Auto (Asia/Kolkata)");
    rerender(
      <TimeZoneField value="Asia/Tokyo" detected="Asia/Kolkata" onChange={vi.fn()} ariaLabel="Time zone" />,
    );
    expect(screen.getByRole("combobox", { name: "Time zone" })).toHaveValue("Asia/Tokyo");
  });

  it("lists UTC exactly once", () => {
    expect(timeZoneChoices().filter((z) => z === "UTC")).toEqual(["UTC"]);
  });
});
