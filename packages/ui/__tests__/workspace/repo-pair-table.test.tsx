import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RepoPairTable, type RepoPairSummary } from "../../src/workspace/repo-pair-table.js";

function pair(
  repo1: string,
  repo2: string,
  filePairCount = 4,
  maxStrength = 6,
): RepoPairSummary {
  return {
    id: `${repo1}↔${repo2}`,
    repo1,
    repo2,
    filePairCount,
    maxStrength,
    lastDate: "2026-06-01",
  };
}

describe("RepoPairTable (virtualized)", () => {
  it("renders a row per pair with both repo names and the file-pair count", () => {
    const rows = [pair("api", "core", 7), pair("ui", "core", 2)];
    render(<RepoPairTable repoPairs={rows} />);
    expect(screen.getByText("api")).toBeInTheDocument();
    expect(screen.getByText("ui")).toBeInTheDocument();
    expect(screen.getAllByText("core").length).toBe(2);
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("invokes onSelectPair when a row is clicked", () => {
    const onSelect = vi.fn();
    render(<RepoPairTable repoPairs={[pair("api", "core")]} onSelectPair={onSelect} />);
    fireEvent.click(screen.getByText("api"));
    expect(onSelect).toHaveBeenCalledWith("api↔core");
  });

  it("shows the empty state when there are no pairs", () => {
    render(<RepoPairTable repoPairs={[]} />);
    expect(screen.getByText(/no repository pairs/i)).toBeInTheDocument();
  });
});
