import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type {
  WorkspaceTestImpactResponse,
  WorkspaceTestRecommendation,
  WorkspaceUnresolvedLink,
} from "@repowise-dev/types/workspace";
import { ContractTestsSection } from "../../src/workspace/contract-tests-section.js";

const CONTRACT = "http::GET::/users";

function rec(over: Partial<WorkspaceTestRecommendation> = {}): WorkspaceTestRecommendation {
  return {
    test_id: "web::tests/users.test.ts::loads",
    test_file: "tests/users.test.ts",
    consumer_repo: "web",
    consumer_files: ["src/client.ts"],
    consumer_symbol_ids: ["src/client.ts::fetchUsers"],
    provider_repo: "api",
    contract_ids: [CONTRACT],
    contract_types: ["http"],
    basis: "measured",
    via: "coverage-map",
    confidence: 0.9,
    source_files: ["src/client.ts"],
    evidence: [],
    ...over,
  };
}

function unresolved(over: Partial<WorkspaceUnresolvedLink> = {}): WorkspaceUnresolvedLink {
  return {
    consumer_repo: "web",
    consumer_file: "src/client.ts",
    consumer_symbol_id: null,
    provider_repo: "api",
    provider_file: "app/routers/users.py",
    contract_id: CONTRACT,
    contract_type: "http",
    reason: "unbound",
    detail: null,
    ...over,
  };
}

function response(over: Partial<WorkspaceTestImpactResponse> = {}): WorkspaceTestImpactResponse {
  return {
    workspace: true,
    recommendations: [],
    recommendations_total: 0,
    recommendations_emitted: 0,
    recommendations_truncated: false,
    recommendations_omitted: 0,
    recommendations_by_basis: {},
    recommendations_by_repo: {},
    recommendations_by_consumer_repo: {},
    unresolved: [],
    files_analyzed: [],
    summary: {},
    ...over,
  };
}

describe("ContractTestsSection", () => {
  it("renders nothing for a null result", () => {
    const { container } = render(<ContractTestsSection result={null} contractId={CONTRACT} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists a measured row under its consumer repo", () => {
    render(
      <ContractTestsSection
        result={response({ recommendations: [rec()] })}
        contractId={CONTRACT}
      />,
    );
    expect(screen.getByRole("heading", { name: "Tests to run" })).toBeTruthy();
    expect(screen.getByText("web")).toBeTruthy();
    expect(screen.getByText("tests/users.test.ts")).toBeTruthy();
    expect(screen.getByText("measured")).toBeTruthy();
    expect(screen.getByText("via the coverage map")).toBeTruthy();
  });

  it("writes an inferred row without a percentage anywhere", () => {
    const { container } = render(
      <ContractTestsSection
        result={response({
          recommendations: [rec({ basis: "inferred", via: "call-graph", confidence: 0.6 })],
        })}
        contractId={CONTRACT}
      />,
    );
    expect(screen.getByText("inferred")).toBeTruthy();
    expect(screen.getByText("via the call graph")).toBeTruthy();
    expect(container.textContent).not.toContain("%");
  });

  it("names the consumer file and the reason for a link it could not follow", () => {
    render(
      <ContractTestsSection
        result={response({ unresolved: [unresolved()] })}
        contractId={CONTRACT}
      />,
    );
    expect(screen.getByRole("heading", { name: "Could not determine" })).toBeTruthy();
    expect(screen.getByText("web / src/client.ts")).toBeTruthy();
    expect(screen.getByText("contract never bound to a symbol")).toBeTruthy();
  });

  it("says the consumers were analyzed when nothing reaches the contract", () => {
    render(<ContractTestsSection result={response()} contractId={CONTRACT} />);
    expect(screen.getByText(/nothing reaches the call sites/)).toBeTruthy();
  });

  it("names each summary reason in words", () => {
    const cases: [string, RegExp][] = [
      ["no_contract_store", /no extracted contracts/],
      ["no_matching_links", /No contract link joins this file/],
      ["no_changed_files", /No provider file was submitted/],
      ["no_contract_data", /carries no contract data/],
      ["lookup_failed", /lookup failed before it could answer/],
    ];
    for (const [reason, words] of cases) {
      const { unmount } = render(
        <ContractTestsSection result={response({ summary: { reason } })} contractId={CONTRACT} />,
      );
      expect(screen.getByText(words)).toBeTruthy();
      unmount();
    }
  });

  it("appends the detail a failed lookup carries", () => {
    render(
      <ContractTestsSection
        result={response({ summary: { reason: "lookup_failed", detail: "TimeoutError" } })}
        contractId={CONTRACT}
      />,
    );
    expect(screen.getByText(/\(TimeoutError\)/)).toBeTruthy();
  });

  it("keys two rows sharing a test id across provider repos apart", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ContractTestsSection
        result={response({
          recommendations: [
            rec({ source_files: ["app/routers/users.py"] }),
            rec({ provider_repo: "billing", source_files: ["app/routers/plans.py"] }),
          ],
        })}
        contractId={CONTRACT}
      />,
    );
    expect(screen.getAllByText("tests/users.test.ts")).toHaveLength(2);
    expect(errors.mock.calls.flat().join(" ")).not.toContain("same key");
    errors.mockRestore();
  });

  it("drops rows belonging to another contract", () => {
    render(
      <ContractTestsSection
        result={response({ recommendations: [rec({ contract_ids: ["http::GET::/orders"] })] })}
        contractId={CONTRACT}
      />,
    );
    expect(screen.queryByText("tests/users.test.ts")).toBeNull();
    expect(screen.getByText(/nothing reaches the call sites/)).toBeTruthy();
  });

  it("renders the host's error wording", () => {
    render(
      <ContractTestsSection result={null} contractId={CONTRACT} error="Test impact is unavailable." />,
    );
    expect(screen.getByText("Test impact is unavailable.")).toBeTruthy();
  });
});
