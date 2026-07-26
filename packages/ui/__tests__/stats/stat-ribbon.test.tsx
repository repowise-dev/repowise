import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatRibbon } from "../../src/stats/stat-ribbon.js";

describe("StatRibbon", () => {
  it("renders each stat as a labelled definition pair", () => {
    render(
      <StatRibbon
        stats={[
          { label: "Lines of code", value: "419.6K" },
          { label: "Files", value: "3,146" },
        ]}
      />,
    );

    expect(screen.getByText("Lines of code")).toBeInTheDocument();
    expect(screen.getByText("419.6K")).toBeInTheDocument();
    expect(screen.getByText("Files")).toBeInTheDocument();
    expect(screen.getByText("3,146")).toBeInTheDocument();
  });

  it("lines figures up as tabular numerals", () => {
    render(<StatRibbon stats={[{ label: "Files", value: "3,146" }]} />);
    expect(screen.getByText("3,146")).toHaveClass("tabular-nums");
  });

  it("drops stats with no value rather than rendering an empty cell", () => {
    render(
      <StatRibbon
        stats={[
          { label: "Files", value: "3,146" },
          { label: "Modules", value: "" },
        ]}
      />,
    );

    expect(screen.getByText("Files")).toBeInTheDocument();
    expect(screen.queryByText("Modules")).not.toBeInTheDocument();
  });

  it("renders nothing at all when every stat is empty", () => {
    const { container } = render(<StatRibbon stats={[{ label: "Files", value: "" }]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
