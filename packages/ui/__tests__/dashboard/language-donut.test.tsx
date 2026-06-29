import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { LanguageDonut } from "../../src/dashboard/language-donut.js";

describe("LanguageDonut", () => {
  it("hides config file formats from the visible language usage", () => {
    render(
      <LanguageDonut
        distribution={{
          Python: 7,
          TypeScript: 3,
          JSON: 5,
          yaml: 4,
          TOML: 1,
        }}
      />,
    );

    expect(screen.getByText("Python")).toBeInTheDocument();
    expect(screen.getByText("TypeScript")).toBeInTheDocument();
    expect(screen.queryByText("JSON")).not.toBeInTheDocument();
    expect(screen.queryByText("yaml")).not.toBeInTheDocument();
    expect(screen.queryByText("TOML")).not.toBeInTheDocument();
    expect(screen.getByText("70%")).toBeInTheDocument();
    expect(screen.getByText("30%")).toBeInTheDocument();
  });

  it("renders nothing when only config file formats are present", () => {
    const { container } = render(
      <LanguageDonut distribution={{ json: 2, YAML: 1, toml: 1 }} />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
