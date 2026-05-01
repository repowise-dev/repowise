import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

describe("@repowise-dev/ui test infrastructure", () => {
  it("mounts a React tree under jsdom and resolves jest-dom matchers", () => {
    function Hello({ name }: { name: string }) {
      return <p data-testid="greeting">Hello, {name}</p>;
    }

    render(<Hello name="repowise" />);

    const node = screen.getByTestId("greeting");
    expect(node).toBeInTheDocument();
    expect(node).toHaveTextContent("Hello, repowise");
  });
});
