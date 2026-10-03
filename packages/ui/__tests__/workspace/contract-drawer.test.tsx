import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { WorkspaceContractLinkEntry } from "@repowise-dev/types/workspace";
import { ContractDrawer } from "../../src/workspace/contract-drawer.js";
import {
  flattenSchemaFields,
  linksForContract,
  schemaFieldConstraints,
  type ContractEntry,
} from "../../src/workspace/contract-facts.js";

it("flattens recursive OpenAPI fields with locations, arrays, and constraints", () => {
  const rows = flattenSchemaFields([
    {
      name: "$body",
      type: "object",
      location: "body",
      children: [
        {
          name: "orders",
          type: "array",
          required: true,
          items: {
            name: "$items",
            type: "object",
            children: [
              { name: "status", type: "string", nullable: true, enum_values: ["new", "done"] },
            ],
          },
        },
      ],
    },
    { name: "trace", type: "string", location: "header" },
  ]);

  expect(rows.map((row) => row.path)).toEqual([
    "body",
    "body.orders",
    "body.orders[]",
    "body.orders[].status",
    "header:trace",
  ]);
  expect(schemaFieldConstraints(rows[3]!.field)).toBe("Optional · Nullable · Enum: new, done");
});

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
  it("renders the contract identity, role, location and how it was found", () => {
    render(
      <ContractDrawer
        contract={contract()}
        open
        onOpenChange={() => {}}
        fullPageHref="/workspace/contracts/detail?id=x"
      />,
    );
    // The verb is its own label and the path wraps at its slashes.
    expect(screen.getAllByText("GET").length).toBeGreaterThan(0);
    expect(screen.getByText("http::GET::/users/{id}")).toBeInTheDocument();
    expect(screen.getByText("HTTP contract / Provider")).toBeInTheDocument();
    expect(screen.getByText("users-api")).toBeInTheDocument();
    // The file reads as a dim directory and its name, with the line after it.
    expect(screen.getByText("users.ts").parentElement).toHaveAttribute(
      "title",
      "src/routes/users.ts:42",
    );
    expect(screen.getByText("92%")).toBeInTheDocument();
    expect(screen.getByText("fastapi")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "AI prompt" })).toBeInTheDocument();
  });

  it("links the file and the counterpart code, and swaps to the counterpart", () => {
    const c = contract();
    const onSelectContract = vi.fn();
    render(
      <ContractDrawer
        contract={c}
        open
        onOpenChange={() => {}}
        links={linksForContract(c, [link()])}
        codeLinks={{
          fileHref: (repo, file) => `/repos/${repo}/files/${file}`,
          symbolHref: (repo, symbolId) => `/repos/${repo}/symbols/${symbolId}`,
        }}
        fullPageHref="/workspace/contracts/detail?id=x"
        onSelectContract={onSelectContract}
      />,
    );
    expect(screen.getByText("users.ts").closest("a")).toHaveAttribute(
      "href",
      "/repos/users-api/files/src/routes/users.ts",
    );
    // The consumer bound to a symbol id, so its code link goes to the symbol page.
    expect(screen.getByRole("link", { name: "Code" })).toHaveAttribute(
      "href",
      "/repos/web-app/symbols/src/api/users-client.ts::fetchUser",
    );
    expect(screen.getByText("fetchUser")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /open the consumer in web-app/i }));
    expect(onSelectContract).toHaveBeenCalledWith({
      repo: "web-app",
      file_path: "src/api/users-client.ts",
      contract_id: "http::GET::/users/{id}",
    });
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

  it("says nothing resolves to a provider without calling it dead", () => {
    render(<ContractDrawer contract={contract()} open onOpenChange={() => {}} links={[]} />);
    expect(screen.getByText("No caller found")).toBeInTheDocument();
    expect(screen.getByText(/neither reading makes it dead code/i)).toBeInTheDocument();
  });

  it("names an unmatched consumer's reason and next step", () => {
    render(
      <ContractDrawer
        contract={contract({ role: "consumer", meta: { host: "api.github.com" } })}
        open
        onOpenChange={() => {}}
        unmatchedReason="external_host"
      />,
    );
    expect(screen.getByText("Outside this workspace")).toBeInTheDocument();
    expect(screen.getByText(/add its repository to the workspace/i)).toBeInTheDocument();
  });

  it("shows loading until a deep-linked contract arrives", () => {
    render(
      <ContractDrawer contract={null} pendingId="http::GET::/x" open onOpenChange={() => {}} loading />,
    );
    expect(screen.getByText("Loading contract...")).toBeInTheDocument();
  });

  it("fires the close callback from the close button", () => {
    const onOpenChange = vi.fn();
    render(<ContractDrawer contract={contract()} open onOpenChange={onOpenChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Close panel" }));
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

  it("finds a consumer that reached its provider under another id", () => {
    const c = contract({
      role: "consumer",
      contract_id: "topic::audit",
      repo: "web-app",
      file_path: "src/api/users-client.ts",
    });
    const bridged = link({ contract_id: "topic::orders", consumer_contract_id: "topic::audit" });
    const provider = contract({ contract_id: "topic::orders" });
    expect(linksForContract(c, [bridged])).toEqual([bridged]);
    expect(linksForContract(provider, [bridged])).toEqual([bridged]);
  });
});
