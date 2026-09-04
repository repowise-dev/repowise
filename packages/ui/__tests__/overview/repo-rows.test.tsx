import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RepoRows, type RepoRow } from "../../src/overview/repo-rows.js";

function row(overrides: Partial<RepoRow> = {}): RepoRow {
  return {
    id: "r1",
    name: "repowise",
    localPath: "/home/me/code/repowise",
    href: "/repos/r1/overview",
    status: "indexed",
    health: 7.4,
    fileCount: 3600,
    hotspotCount: 364,
    docPageCount: 4059,
    docFreshPageCount: 3797,
    deadExportCount: 19,
    updatedAt: null,
    indexBehind: false,
    ...overrides,
  };
}

describe("RepoRows", () => {
  it("gives every figure the noun it counts", () => {
    render(<RepoRows repos={[row()]} />);

    // "3,600" alone reads as nothing in particular. The unit is what makes it
    // a fact rather than a decoration.
    expect(screen.getByText(/3,600 files/)).toBeTruthy();
    expect(screen.getByText(/364 hotspots/)).toBeTruthy();
    expect(screen.getByText(/19 unused exports/)).toBeTruthy();
    expect(screen.getByText(/94% of 4,059 doc pages fresh/)).toBeTruthy();
  });

  it("renders no marker when a repo is current", () => {
    // A badge every row carries says nothing. A quiet list is a healthy list.
    render(<RepoRows repos={[row()]} />);

    expect(screen.queryByText("Index behind HEAD")).toBeNull();
    expect(screen.queryByText("Not indexed yet")).toBeNull();
  });

  it("marks a repo whose index is behind the checkout", () => {
    render(<RepoRows repos={[row({ indexBehind: true })]} />);

    expect(screen.getByText("Index behind HEAD")).toBeTruthy();
  });

  it("treats an unknown freshness comparison as unremarkable", () => {
    // `null` means the check could not run, which is not the same as behind.
    render(<RepoRows repos={[row({ indexBehind: null })]} />);

    expect(screen.queryByText("Index behind HEAD")).toBeNull();
  });

  it("shows a dash rather than a zero for a repo with no health snapshot", () => {
    // A 0.0 in red would report "analysed, and terrible" about a repository
    // nobody has analysed.
    render(<RepoRows repos={[row({ health: null })]} />);

    expect(screen.queryByText("0.0")).toBeNull();
    expect(screen.getByText("—")).toBeTruthy();
  });

  it("bands the score on the same five-step ladder the repo pages use", () => {
    render(
      <RepoRows
        repos={[
          row({ id: "a", name: "alpha", health: 9.1 }),
          row({ id: "b", name: "beta", health: 7.4 }),
          row({ id: "c", name: "gamma", health: 5.0 }),
          row({ id: "d", name: "delta", health: 3.6 }),
          row({ id: "e", name: "epsilon", health: 3.0 }),
        ]}
      />,
    );

    expect(screen.getByText("Excellent")).toBeTruthy();
    expect(screen.getByText("Good")).toBeTruthy();
    expect(screen.getByText("Fair")).toBeTruthy();
    expect(screen.getByText("Needs work")).toBeTruthy();
    expect(screen.getByText("Critical")).toBeTruthy();
  });

  // The bug this guards: 7.4 read amber "Warning" in the workspace list and
  // green "Good" the moment you opened the same repo.
  it("reads a mid-seven score green, as the repo overview does", () => {
    render(<RepoRows repos={[row({ health: 7.4 })]} />);

    expect(screen.getByText("Good")).toBeTruthy();
    expect(screen.queryByText("Warning")).toBeNull();
  });

  it("does not quote figures for a repo that was never indexed", () => {
    render(
      <RepoRows repos={[row({ status: "needs_index", health: null, fileCount: 0 })]} />,
    );

    expect(screen.getByText("Not indexed yet")).toBeTruthy();
    expect(screen.getByText(/Run an index to see/)).toBeTruthy();
    expect(screen.queryByText(/0 files/)).toBeNull();
  });

  it("renders row actions outside the row link", () => {
    // A button nested in an anchor is invalid and swallows its own clicks, so
    // the two are siblings. This is the assertion that catches a regression
    // back to the nested form.
    const { container } = render(
      <RepoRows repos={[row()]} actionsFor={() => <button type="button">Delete</button>} />,
    );

    const anchor = container.querySelector("a");
    expect(anchor).toBeTruthy();
    expect(anchor?.querySelector("button")).toBeNull();
    expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
  });

  it("does not truncate the repository name", () => {
    // A title needing an ellipsis means the layout is wrong, and a directory
    // name is exactly the string somebody is scanning for.
    const long = "a-very-long-monorepo-name-that-would-be-cut-by-a-fixed-width-column";
    const { container } = render(<RepoRows repos={[row({ name: long })]} />);

    expect(screen.getByText(long)).toBeTruthy();
    expect(container.querySelector(".truncate")).toBeNull();
  });
});
