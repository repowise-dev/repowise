/**
 * EntityHoverCard renders curated file metadata (viewer plan C-4):
 * summary + tags above the existing ownership/churn rows.
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { EntityHoverCard } from "../../src/shared/entity";

describe("EntityHoverCard file meta", () => {
  it("shows curated summary and tags when open", () => {
    const { getByText, getAllByText } = render(
      <EntityHoverCard
        entity={{ kind: "file", id: "src/main.py" }}
        meta={{
          kind: "file",
          data: {
            summary: "SENTINEL: CLI entry point wiring commands.",
            tags: ["entry_point", "python"],
            owner: "dev1",
            language: "python",
          },
        }}
        defaultOpen
      >
        <button type="button">main.py</button>
      </EntityHoverCard>,
    );

    expect(getByText("SENTINEL: CLI entry point wiring commands.")).toBeDefined();
    expect(getByText("entry_point")).toBeDefined();
    // "python" appears as both a tag pill and the Language row value.
    expect(getAllByText("python").length).toBeGreaterThanOrEqual(2);
    expect(getByText("dev1")).toBeDefined();
    expect(getByText("src/main.py")).toBeDefined();
  });

  it("degrades gracefully without curated fields", () => {
    const { getByText, queryByText } = render(
      <EntityHoverCard
        entity={{ kind: "file", id: "src/other.py" }}
        meta={{ kind: "file", data: { owner: "dev2" } }}
        defaultOpen
      >
        <span>other.py</span>
      </EntityHoverCard>,
    );

    expect(getByText("src/other.py")).toBeDefined();
    expect(getByText("dev2")).toBeDefined();
    expect(queryByText(/SENTINEL/)).toBeNull();
  });
});
