import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { WorkspaceContractLinkEntry } from "@repowise-dev/types/workspace";
import { ContractDrawer } from "../../src/workspace/contract-drawer.js";
import { linksForContract, type ContractEntry } from "../../src/workspace/contract-facts.js";

function contract(overrides: Partial<ContractEntry> = {}): ContractEntry {
  return {
    contract_id: "http::GET::/users/{id}",
    contract_type: "http",
    role: "provider",
    repo: "users-api",
    file_path: "src/routes/users.ts",
    symbol_name: "fastapi::get_user",
    confidence: 0.92,
    service: "users",
    line: 42,
    symbol_id: "src/routes/users.ts::get_user",
    meta: { method: "GET", path: "/users/{id}", framework: "fastapi" },
    ...overrides,
  };
}

function link(overrides: Partial<WorkspaceContractLinkEntry> = {}): WorkspaceContractLinkEntry {
  return {
    contract_id: "http::GET::/users/{id}",
    contract_type: "http",
    match_type: "exact",
    confidence: 0.8,
    provider_repo: "users-api",
    provider_file: "src/routes/users.ts",
    provider_symbol: "get_user",
    consumer_repo: "web-app",
    consumer_file: "src/api/users-client.ts",
    consumer_symbol: "fetchUser",
    provider_service: "users",
    consumer_service: "web",
    provider_symbol_id: null,
    consumer_symbol_id: "src/api/users-client.ts::fetchUser",
    ...overrides,
  };
}

describe("ContractDrawer", () => {
  it("renders the contract identity, role, location and confidence", () => {
    render(
      <ContractDrawer
        contract={contract()}
        open
        onOpenChange={() => {}}
        fullPageHref="/workspace/contracts/detail?id=x"
      />,
    );
    expect(screen.getByText("GET /users/{id}")).toBeInTheDocument();
    expect(screen.getByText("http::GET::/users/{id}")).toBeInTheDocument();
    // Type and role share the eyebrow line, so they are read off it together.
    expect(screen.getByText(/HTTP contract/).textContent).toContain("Provider");
    expect(screen.getByText("users-api")).toBeInTheDocument();
    expect(screen.getByText("src/routes/users.ts")).toBeInTheDocument();
    expect(screen.getByText("line 42")).toBeInTheDocument();
    expect(screen.getByText("92%")).toBeInTheDocument();
    expect(screen.getByText("fastapi")).toBeInTheDocument();
  });

  it("links the file and the counterpart symbol with the supplied hrefs", () => {
    const c = contract();
    const links = [link()];
    render(
      <ContractDrawer
        contract={c}
        open
        onOpenChange={() => {}}
        links={linksForContract(c, links)}
        codeLinks={{
          fileHref: (repo, file) => `/repos/${repo}/files/${file}`,
          symbolHref: (repo, symbolId) => `/repos/${repo}/symbols/${symbolId}`,
        }}
        fullPageHref="/workspace/contracts/detail?id=x"
      />,
    );
    expect(screen.getByText("src/routes/users.ts").closest("a")).toHaveAttribute(
      "href",
      "/repos/users-api/files/src/routes/users.ts",
    );
    // The consumer bound to a symbol id, so its link goes to the symbol page.
    expect(screen.getByText("src/api/users-client.ts").closest("a")).toHaveAttribute(
      "href",
      "/repos/web-app/symbols/src/api/users-client.ts::fetchUser",
    );
    expect(screen.getByText("web-app")).toBeInTheDocument();
    expect(screen.getByText("fetchUser")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open full page" })).toHaveAttribute(
      "href",
      "/workspace/contracts/detail?id=x",
    );
  });

  it("renders schema fields when a reader recovered them", () => {
    render(
      <ContractDrawer
        contract={contract()}
        open
        onOpenChange={() => {}}
        schema={{
          source: "signature",
          request_fields: [{ name: "user_id", type: "str", required: true }],
          response_fields: [],
        }}
      />,
    );
    expect(screen.getByText("Request")).toBeInTheDocument();
    expect(screen.getByText("user_id")).toBeInTheDocument();
    expect(screen.getByText("str")).toBeInTheDocument();
    expect(screen.getByText("Required")).toBeInTheDocument();
  });

  it("says nothing resolves when the contract has no links", () => {
    render(<ContractDrawer contract={contract()} open onOpenChange={() => {}} links={[]} />);
    expect(screen.getByText("Callers")).toBeInTheDocument();
    expect(screen.getByText(/nothing in this workspace resolves/i)).toBeInTheDocument();
  });

  it("fires the close callback from the close button", () => {
    const onOpenChange = vi.fn();
    render(<ContractDrawer contract={contract()} open onOpenChange={onOpenChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Close contract" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

describe("linksForContract", () => {
  it("keeps only the links whose matching side is this declaration", () => {
    const c = contract();
    const mine = link();
    const otherFile = link({ provider_file: "src/routes/legacy.ts" });
    const otherId = link({ contract_id: "http::POST::/users" });
    expect(linksForContract(c, [mine, otherFile, otherId])).toEqual([mine]);
  });

  it("matches on the consumer side for a consumer contract", () => {
    const c = contract({
      role: "consumer",
      repo: "web-app",
      file_path: "src/api/users-client.ts",
    });
    const mine = link();
    expect(linksForContract(c, [mine])).toEqual([mine]);
  });
});
