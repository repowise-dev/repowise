/** Each listed test carries the server's reason for listing it, when it sent one. */
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import type { RecommendationValidation } from "@repowise-dev/types/refactoring";

import { ValidationSummary } from "../../src/refactoring/validation-summary";

const validation: RecommendationValidation = {
  basis: "inferred",
  via: "call-graph",
  total: 2,
  tests: ["tests/test_walker.py", "tests/test_other.py"],
  truncated: false,
  affected_files: [],
  affected_symbols: [],
  commands: [],
  targets: [],
  reasons: { "tests/test_walker.py": "calls walk_file" },
};

describe("ValidationSummary", () => {
  it("shows a test's reason under it, and nothing for a test without one", () => {
    const { getAllByRole } = render(<ValidationSummary validation={validation} />);
    const rows = getAllByRole("listitem").map((row) => row.textContent);
    expect(rows).toEqual(["tests/test_walker.pycalls walk_file", "tests/test_other.py"]);
  });

  it("says what to do first when no test reaches the change", () => {
    const step = "No test reaches this; add a characterization test for `walk` before the edit.";
    const { getByText } = render(
      <ValidationSummary
        validation={{ ...validation, basis: "unknown", via: null, total: 0, tests: [], prerequisite: step }}
      />,
    );
    expect(getByText(step)).toBeTruthy();
  });
});
