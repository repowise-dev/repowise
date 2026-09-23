import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { WorkspaceContractLinkEntry } from "@repowise-dev/types/workspace";
import { ContractLinksTable } from "../../src/workspace/contract-links-table.js";

function link(
  overrides: Partial<WorkspaceContractLinkEntry> = {},
): WorkspaceContractLinkEntry {
  return {
    contract_id: "http::GET::/users/{id}",
    contract_type: "http",
    match_type: "exact",
    confidence: 0.9,
    provider_repo: "users-api",
    provider_file: "src/routes/users.ts",
    provider_symbol: "getUser",
    consumer_repo: "web-app",
    consumer_file: "src/api/users-client.ts",
    consumer_symbol: "fetchUser",
    provider_service: null,
    consumer_service: null,
    provider_symbol_id: null,
    consumer_symbol_id: null,
    ...overrides,
  };
}

// The table and its stacked-card rendering are both in the DOM (CSS picks one),
// so every cell appears twice.
describe("ContractLinksTable", () => {
  it("renders the method as a label and the path apart from it", () => {
    render(
      <ContractLinksTable
        links={[
          link(),
          link({ contract_id: "http::POST::/orders", provider_repo: "orders-api", consumer_repo: "checkout" }),
        ]}
      />,
    );
    expect(screen.getAllByText("GET").length).toBeGreaterThan(0);
    expect(screen.getAllByText("/users/{id}").length).toBeGreaterThan(0);
    expect(screen.getAllByText("POST").length).toBeGreaterThan(0);
    expect(screen.getAllByText("orders-api").length).toBeGreaterThan(0);
  });

  it("splits a file into a dim directory and its name, with the full path on hover", () => {
    render(<ContractLinksTable links={[link()]} />);
    const name = screen.getAllByText("users.ts")[0]!;
    expect(name.parentElement).toHaveAttribute("title", "src/routes/users.ts");
    expect(screen.getAllByText("users-client.ts").length).toBeGreaterThan(0);
  });

  it("renders the type and the confidence as a plain figure", () => {
    render(<ContractLinksTable links={[link({ confidence: 0.75, contract_type: "socket" })]} />);
    expect(screen.getAllByText("Socket").length).toBeGreaterThan(0);
    expect(screen.getAllByText("75%").length).toBeGreaterThan(0);
  });

  it("opens a link from the row", () => {
    const onSelect = vi.fn();
    const l = link();
    render(<ContractLinksTable links={[l]} onSelect={onSelect} />);
    fireEvent.click(screen.getAllByText("users.ts")[0]!);
    expect(onSelect).toHaveBeenCalledWith(l);
  });

  it("shows the empty state when there are no links", () => {
    render(<ContractLinksTable links={[]} />);
    expect(screen.getByText(/no matched contract links/i)).toBeInTheDocument();
  });
});

describe("ContractLinksTable order", () => {
  it("puts the strongest links first, then sorts by contract", () => {
    render(
      <ContractLinksTable
        links={[
          link({ contract_id: "http::GET::/weak", confidence: 0.65 }),
          link({ contract_id: "http::GET::/b", confidence: 0.9 }),
          link({ contract_id: "http::GET::/a", confidence: 0.9 }),
        ]}
      />,
    );
    // The table body's identities in row order; the stacked cards repeat them outside tbody.
    const ids = [...document.querySelectorAll("tbody [title^='http::']")].map((el) =>
      el.getAttribute("title"),
    );
    expect(ids).toEqual(["http::GET::/a", "http::GET::/b", "http::GET::/weak"]);
  });
});
