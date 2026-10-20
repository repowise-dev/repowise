import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CiHint, CI_GUIDE_URL } from "../../src/shared/ci-hint.js";
import { SecurityFindingsTable } from "../../src/security/findings-table.js";

describe("CiHint", () => {
  it("names the gate and links the CI guide", () => {
    render(<CiHint command="repowise doc-drift --check" checks="the documentation" />);
    expect(screen.getByText("repowise doc-drift --check")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Set it up" })).toHaveAttribute("href", CI_GUIDE_URL);
  });

  it("is offered when a repository has no security findings", () => {
    render(<SecurityFindingsTable findings={[]} />);
    expect(screen.getByText("repowise security check")).toBeInTheDocument();
  });
});
