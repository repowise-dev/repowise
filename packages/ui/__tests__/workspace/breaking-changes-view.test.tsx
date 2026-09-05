import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { BreakingChange, BreakingChangeReport } from "@repowise-dev/types";
import { BreakingChangesView } from "../../src/workspace/breaking-changes-view.js";
import { SystemMapBreakingPanel } from "../../src/workspace/system-map/system-map-breaking-panel.js";

function change(over: Partial<BreakingChange> = {}): BreakingChange {
  return {
    kind: "removed_endpoint",
    severity: "breaking",
    contract_id: "http::GET::/users",
    contract_type: "http",
    provider_repo: "api",
    provider_file: "routes.py",
    provider_symbol: "handler",
    provider_symbol_id: "routes.py::handler",
    provider_service: null,
    provider_node_id: "api",
    detail: "http::GET::/users was removed",
    impacted_consumers: [
      {
        repo: "web",
        service: null,
        node_id: "web",
        file: "client.ts",
        symbol: "fetchUser",
        symbol_id: "client.ts::fetchUser",
        match_type: "exact",
        confidence: 0.9,
      },
    ],
    ...over,
  };
}

function report(
  changes: BreakingChange[],
  over: Partial<BreakingChangeReport> = {},
): BreakingChangeReport {
  return {
    version: 1,
    generated_at: "2026-09-04T00:00:00Z",
    changes,
    total: changes.length,
    breaking_count: changes.filter((c) => c.severity === "breaking").length,
    warning_count: changes.filter((c) => c.severity === "warning").length,
    impacted_repos: ["web"],
    impacted_services: ["web"],
    total_impacted_consumers: changes.length,
    ...over,
  };
}

const links = {
  symbolHref: (repo: string, symbolId: string) =>
    `/repos/${repo}/symbols/${encodeURIComponent(symbolId)}`,
  fileHref: (repo: string, file: string) => `/repos/${repo}/files/${file}`,
};

describe("BreakingChangesView", () => {
  it("separates a report that never ran from one with no changes", () => {
    const { unmount } = render(<BreakingChangesView report={null} />);
    expect(screen.getByText(/has not run/i)).toBeInTheDocument();
    unmount();

    const noTimestamp = render(
      <BreakingChangesView report={report([], { generated_at: null })} />,
    );
    expect(screen.getByText(/has not run/i)).toBeInTheDocument();
    noTimestamp.unmount();

    render(<BreakingChangesView report={report([])} />);
    expect(screen.getByText(/no breaking changes/i)).toBeInTheDocument();
  });

  it("says it is still checking while loading", () => {
    render(<BreakingChangesView report={null} loading />);
    expect(screen.getByText(/checking the latest update/i)).toBeInTheDocument();
  });

  it("summarises the counts and the impacted repos", () => {
    render(<BreakingChangesView report={report([change()])} />);
    expect(screen.getByText(/1 breaking, 0 warnings across 1 repo/i)).toBeInTheDocument();
  });

  it("links the provider symbol and the consumer symbol when hrefs are supplied", () => {
    render(<BreakingChangesView report={report([change()])} links={links} />);

    const provider = screen.getByText("routes.py");
    expect(provider.tagName).toBe("A");
    expect(provider).toHaveAttribute(
      "href",
      "/repos/api/symbols/routes.py%3A%3Ahandler",
    );

    const consumer = screen.getByText(/client\.ts/);
    expect(consumer.tagName).toBe("A");
    expect(consumer).toHaveAttribute(
      "href",
      "/repos/web/symbols/client.ts%3A%3AfetchUser",
    );
  });

  it("falls back to the file route when a side has no symbol id", () => {
    const c = change({
      provider_symbol_id: null,
      impacted_consumers: [
        {
          repo: "web",
          service: null,
          node_id: "web",
          file: "client.ts",
          symbol: "fetchUser",
          symbol_id: null,
          match_type: "exact",
          confidence: 0.9,
        },
      ],
    });
    render(<BreakingChangesView report={report([c])} links={links} />);
    expect(screen.getByText("routes.py")).toHaveAttribute("href", "/repos/api/files/routes.py");
    expect(screen.getByText(/client\.ts/)).toHaveAttribute("href", "/repos/web/files/client.ts");
  });

  it("renders plain text when no href builders are supplied", () => {
    const { container } = render(<BreakingChangesView report={report([change()])} />);
    expect(container.querySelectorAll("a")).toHaveLength(0);
    expect(screen.getByText(/routes\.py/)).toBeInTheDocument();
    expect(screen.getByText(/client\.ts/)).toBeInTheDocument();
  });

  it("leaves a side unlinked when the host cannot route it", () => {
    const { container } = render(
      <BreakingChangesView
        report={report([change()])}
        links={{ symbolHref: () => null, fileHref: () => null }}
      />,
    );
    expect(container.querySelectorAll("a")).toHaveLength(0);
    expect(screen.getByText(/routes\.py/)).toBeInTheDocument();
  });

  it("sorts breaking before warning whatever order the report holds", () => {
    const warning = change({
      severity: "warning",
      contract_id: "http::GET::/orders",
      kind: "field_type_changed",
      detail: "orders response field changed type",
    });
    const { container } = render(
      <BreakingChangesView report={report([warning, change()])} />,
    );
    const ids = Array.from(container.querySelectorAll("button"))
      .map((b) => b.textContent ?? "")
      .filter((t) => t.startsWith("http::"));
    expect(ids).toEqual(["http::GET::/users", "http::GET::/orders"]);
  });

  it("hands the contract back to the host when its id is clicked", () => {
    const onSelectContract = vi.fn();
    render(
      <BreakingChangesView report={report([change()])} onSelectContract={onSelectContract} />,
    );
    fireEvent.click(screen.getByText("http::GET::/users"));
    expect(onSelectContract).toHaveBeenCalledWith("http::GET::/users", expect.anything());
  });
});

describe("SystemMapBreakingPanel through the shared row", () => {
  it("keeps both code sides as node-selecting buttons, not links", () => {
    const onSelectNode = vi.fn();
    const { container } = render(
      <SystemMapBreakingPanel
        report={report([change()])}
        onSelectNode={onSelectNode}
        onClear={() => {}}
      />,
    );
    expect(container.querySelectorAll("a")).toHaveLength(0);

    fireEvent.click(screen.getByText("http::GET::/users"));
    expect(onSelectNode).toHaveBeenCalledWith("api");

    fireEvent.click(screen.getByText(/client\.ts/));
    expect(onSelectNode).toHaveBeenCalledWith("web");
  });
});
