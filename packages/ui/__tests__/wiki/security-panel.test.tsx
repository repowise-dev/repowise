import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { SecurityFinding } from "@repowise-dev/types";
import { SecurityPanel } from "../../src/wiki/security-panel.js";

const finding: SecurityFinding = {
  id: 1,
  file_path: "src/auth.py",
  kind: "hardcoded_secret",
  severity: "high",
  snippet: "API_KEY = \"sk-1234\"",
  detected_at: "2026-01-01T00:00:00Z",
};

describe("SecurityPanel", () => {
  it("renders empty state when no findings", () => {
    render(<SecurityPanel findings={[]} />);
    expect(screen.getByText(/no security signals/i)).toBeInTheDocument();
  });

  it("renders nothing while loading", () => {
    const { container } = render(<SecurityPanel findings={undefined} isLoading />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a finding with severity badge and kind", () => {
    render(<SecurityPanel findings={[finding]} />);
    expect(screen.getByText("High")).toBeInTheDocument();
    expect(screen.getByText("hardcoded_secret")).toBeInTheDocument();
  });
});
