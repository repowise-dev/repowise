import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { WorkspaceContractLinkEntry } from "@repowise-dev/types/workspace";
import { ContractLinksTable } from "../../src/workspace/contract-links-table.js";

function link(
  overrides: Partial<WorkspaceContractLinkEntry> = {},
): WorkspaceContractLinkEntry {
  return {
    contract_id: "GET /users/{id}",
    contract_type: "http",
    match_type: "exact",
    confidence: 0.9,
    provider_repo: "users-api",
    provider_file: "src/routes/users.ts",
    provider_symbol: "getUser",
    consumer_repo: "web-app",
    consumer_file: "src/api/users-client.ts",
    consumer_symbol: "fetchUser",
    ...overrides,
  };
}

describe("ContractLinksTable (virtualized)", () => {
  it("renders a row per link with key cells", () => {
    const links = [
      link(),
      link({
        contract_id: "POST /orders",
        provider_repo: "orders-api",
        consumer_repo: "checkout",
      }),
    ];
    render(<ContractLinksTable links={links} />);
    expect(screen.getByText("GET /users/{id}")).toBeInTheDocument();
    expect(screen.getByText("POST /orders")).toBeInTheDocument();
    expect(screen.getByText("users-api")).toBeInTheDocument();
    expect(screen.getByText("orders-api")).toBeInTheDocument();
  });

  it("renders the contract type badge and confidence percentage", () => {
    render(<ContractLinksTable links={[link({ confidence: 0.75 })]} />);
    expect(screen.getByText("HTTP")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
  });

  it("shows the provider and consumer file paths", () => {
    render(<ContractLinksTable links={[link()]} />);
    expect(screen.getByText("src/routes/users.ts")).toBeInTheDocument();
    expect(screen.getByText("src/api/users-client.ts")).toBeInTheDocument();
  });

  it("shows the empty state when there are no links", () => {
    render(<ContractLinksTable links={[]} />);
    expect(
      screen.getByText(/no matched contract links/i),
    ).toBeInTheDocument();
  });
});
