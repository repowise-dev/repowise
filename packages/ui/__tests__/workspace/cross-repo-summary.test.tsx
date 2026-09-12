import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CrossRepoSummary } from "../../src/workspace/cross-repo-summary.js";

describe("CrossRepoSummary", () => {
  it("makes bounded Maven non-matches visible", () => {
    render(
      <CrossRepoSummary
        crossRepo={{
          co_change_count: 0,
          package_dep_count: 0,
          package_diagnostic_count: 240,
          package_diagnostics_emitted: 200,
          package_diagnostic_codes: ["external_coordinate"],
          top_connections: [],
        }}
        contracts={null}
      />,
    );

    expect(
      screen.getByText("240 Maven non-matches; 200 retained"),
    ).toBeInTheDocument();
  });
});
