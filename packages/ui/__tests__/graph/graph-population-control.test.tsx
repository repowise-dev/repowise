import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GraphPopulationControl } from "../../src/graph/graph-population-control.js";
import { PRODUCTION_ONLY } from "@repowise-dev/types/graph";

const breakdown = {
  total: 4104,
  visible: 2427,
  tests: 1398,
  examples: 27,
  docs: 293,
};

describe("GraphPopulationControl", () => {
  it("summarises what is counted and offers each kind with its count", () => {
    const onChange = vi.fn();
    render(
      <GraphPopulationControl
        population={PRODUCTION_ONLY}
        breakdown={breakdown}
        onChange={onChange}
      />,
    );
    const trigger = screen.getByRole("button", { name: /Files counted/ });
    expect(trigger.textContent).toContain("2,427 of 4,104 files");

    fireEvent.click(trigger);
    expect(screen.getByText("1,398")).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/Tests/));
    expect(onChange).toHaveBeenCalledWith({ tests: true, examples: false, docs: false });
  });

  it("says all files when nothing is hidden", () => {
    render(
      <GraphPopulationControl
        population={{ tests: true, examples: true, docs: true }}
        breakdown={{ ...breakdown, visible: 4104 }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /Files counted/ }).textContent).toContain(
      "All 4,104 files",
    );
  });

  it("degrades to labels without counts when the breakdown is absent", () => {
    render(<GraphPopulationControl population={PRODUCTION_ONLY} onChange={vi.fn()} />);
    const trigger = screen.getByRole("button", { name: /Files counted/ });
    expect(trigger.textContent).toContain("Production files");
    fireEvent.click(trigger);
    expect(screen.getByLabelText(/Docs and config/)).toBeTruthy();
    expect(screen.queryByText("293")).toBeNull();
  });
});
