import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DecisionAgentPrompts } from "../../src/decisions/decision-agent-prompts";

describe("DecisionAgentPrompts", () => {
  it("copies the server's text for the prompt clicked", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(
      <DecisionAgentPrompts
        discover={{ text: "check these 12", label: "Check background records" }}
        agreements={{ text: "move these", label: "Move agreements" }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Check background records" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("check these 12"));
  });

  it("offers only the prompts that have text", () => {
    render(
      <DecisionAgentPrompts
        discover={null}
        agreements={{ text: "", label: "Move agreements" }}
        add={{ text: "record them", label: "Add decisions" }}
      />,
    );

    expect(screen.getByRole("button", { name: "Add decisions" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Move agreements" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check background records" })).not.toBeInTheDocument();
  });

  it("renders nothing with no prompt", () => {
    const { container } = render(<DecisionAgentPrompts />);
    expect(container).toBeEmptyDOMElement();
  });
});
