import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { DocPageVersion } from "@repowise-dev/types";
import { VersionHistory } from "../../src/wiki/version-history.js";

const version: DocPageVersion = {
  id: "v1",
  page_id: "p1",
  version: 1,
  page_type: "module",
  title: "Module",
  content: "old content",
  source_hash: "abc",
  model_name: "gpt-5.4-mini",
  provider_name: "openai",
  input_tokens: 100,
  output_tokens: 50,
  confidence: 0.9,
  archived_at: "2026-01-01T00:00:00Z",
};

describe("VersionHistory", () => {
  it("renders nothing when there are no prior versions", () => {
    const { container } = render(
      <VersionHistory versions={[]} currentVersion={2} currentContent="new content" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the collapsed header with a versions count badge", () => {
    render(
      <VersionHistory
        versions={[version]}
        currentVersion={2}
        currentContent="new content"
      />,
    );
    expect(screen.getByText(/version history/i)).toBeInTheDocument();
    expect(screen.getByText(/1 versions/i)).toBeInTheDocument();
  });

  it("expands to show the current and prior version rows", () => {
    render(
      <VersionHistory
        versions={[version]}
        currentVersion={2}
        currentContent="new content"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /version history/i }));
    expect(screen.getByText("v2")).toBeInTheDocument();
    expect(screen.getByText("v1")).toBeInTheDocument();
    expect(screen.getByText(/current/i)).toBeInTheDocument();
  });
});
