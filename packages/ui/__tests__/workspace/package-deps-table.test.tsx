import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { WorkspacePackageDepEntry } from "@repowise-dev/types/workspace";
import { PackageDepsTable } from "../../src/workspace/package-deps-table.js";

function dep(
  overrides: Partial<WorkspacePackageDepEntry> = {},
): WorkspacePackageDepEntry {
  return {
    source_repo: "web-app",
    source_manifest: "package.json",
    target_repo: "shared-lib",
    target_package: "@acme/shared",
    kind: "dependency",
    ...overrides,
  };
}

describe("PackageDepsTable (virtualized)", () => {
  it("renders a row per dependency with key cells", () => {
    const deps = [
      dep(),
      dep({
        source_repo: "api",
        source_manifest: "pyproject.toml",
        target_repo: "core",
        kind: "devDependency",
      }),
    ];
    render(<PackageDepsTable deps={deps} />);
    expect(screen.getByText("web-app")).toBeInTheDocument();
    expect(screen.getByText("shared-lib")).toBeInTheDocument();
    expect(screen.getByText("package.json")).toBeInTheDocument();
    expect(screen.getByText("dependency")).toBeInTheDocument();
    expect(screen.getByText("pyproject.toml")).toBeInTheDocument();
    expect(screen.getByText("devDependency")).toBeInTheDocument();
  });

  it("shows the empty state when there are no deps", () => {
    render(<PackageDepsTable deps={[]} />);
    expect(
      screen.getByText(/no cross-repo package dependencies/i),
    ).toBeInTheDocument();
  });
});
