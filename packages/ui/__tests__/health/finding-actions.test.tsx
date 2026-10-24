import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FindingActions } from "../../src/health/finding-actions.js";
import { AGENT_PROMPT_LABEL } from "../../src/health/ai-prompt-action.js";

const ACTIONS = [
  { kind: "add-tests", target_test: "tests/test_router.py", prompt: "Add tests for `src/router.py`." },
  { kind: "suppress", how: '.repowise/health-rules.json: {"rules": []}' },
];

describe("FindingActions", () => {
  it("leads with the copy-prompt, then the test to extend and the suppress rule", () => {
    render(<FindingActions details={{ actions: ACTIONS }} filePath="src/router.py" />);
    expect(screen.getByRole("button", { name: AGENT_PROMPT_LABEL })).toBeInTheDocument();
    expect(screen.getByText("tests/test_router.py")).toBeInTheDocument();
    expect(screen.getByText(/health-rules\.json/)).toBeInTheDocument();
  });

  it("renders nothing for a finding with no actions", () => {
    const { container } = render(<FindingActions details={{ crap: 40 }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
