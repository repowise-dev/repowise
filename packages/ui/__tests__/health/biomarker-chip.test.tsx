import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BiomarkerChip } from "../../src/health/biomarker-chip.js";
import { biomarkerInfo } from "../../src/health/biomarker-glossary.js";

describe("BiomarkerChip", () => {
  it("opens the glossary entry in a popover on click", () => {
    const info = biomarkerInfo("brain_method");
    render(<BiomarkerChip type="brain_method" />);
    const trigger = screen.getByRole("button", { name: `What is ${info.label}?` });
    expect(screen.queryByText(info.description)).toBeNull();

    fireEvent.click(trigger);
    expect(screen.getByText(info.description)).toBeInTheDocument();
  });

  it("renders a plain label when the glossary has no description", () => {
    render(<BiomarkerChip type="totally_unknown_marker" />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("totally unknown marker")).toBeInTheDocument();
  });

  it("renders a plain label with showInfo=false", () => {
    render(<BiomarkerChip type="brain_method" showInfo={false} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
