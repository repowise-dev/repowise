import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { OrphanProvider, UnmatchedConsumer } from "@repowise-dev/types/workspace";
import { ContractAttention } from "../../src/workspace/contract-attention.js";
import { groupOrphanProviders, parseContractId } from "../../src/workspace/contract-facts.js";

const unmatched = (reason: UnmatchedConsumer["reason"], i: number): UnmatchedConsumer => ({
  repo: "web",
  file_path: `src/api/c${i}.ts`,
  contract_id: `http::GET::/r/${reason}/${i}`,
  contract_type: "http",
  reason,
});

const orphan = (repo: string, type: string, i: number): OrphanProvider => ({
  repo,
  file_path: `src/p${i}.py`,
  contract_id: `${type}::x${i}`,
  contract_type: type,
});

describe("ContractAttention", () => {
  it("opens actionable reasons first and keeps the rest closed", () => {
    render(
      <ContractAttention
        unmatched={[unmatched("external_host", 1), unmatched("no_provider", 2)]}
        orphanGroups={[]}
        onSelect={() => {}}
        browseHref={() => "#"}
      />,
    );
    const headings = screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent);
    expect(headings[0]).toContain("No provider found");
    expect(screen.getByText("/r/no_provider/2")).toBeInTheDocument();
    expect(screen.queryByText("/r/external_host/1")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show the 1 call" }));
    expect(screen.getByText("/r/external_host/1")).toBeInTheDocument();
  });

  it("opens a row in the drawer", () => {
    const onSelect = vi.fn();
    render(
      <ContractAttention
        unmatched={[unmatched("no_provider", 1)]}
        orphanGroups={[]}
        onSelect={onSelect}
        browseHref={() => "#"}
      />,
    );
    fireEvent.click(screen.getByText("/r/no_provider/1"));
    expect(onSelect).toHaveBeenCalledWith({
      repo: "web",
      file_path: "src/api/c1.ts",
      contract_id: "http::GET::/r/no_provider/1",
    });
  });

  it("summarises providers with no caller per repository and type, largest first", () => {
    render(
      <ContractAttention
        unmatched={[]}
        orphanGroups={groupOrphanProviders(
          [orphan("api", "http", 1), orphan("db", "data", 2), orphan("db", "data", 3)],
          1,
        )}
        onSelect={() => {}}
        browseHref={(repo, type) => `/list?repo=${repo}&type=${type}`}
      />,
    );
    expect(screen.getByText("3 providers have no caller in this workspace")).toBeInTheDocument();
    const browse = screen.getAllByRole("link", { name: "Browse" });
    expect(browse[0]).toHaveAttribute("href", "/list?repo=db&type=data");
    expect(browse[1]).toHaveAttribute("href", "/list?repo=api&type=http");
  });
});

describe("parseContractId", () => {
  it("splits http, code and data ids", () => {
    expect(parseContractId("http::GET::/a/{param}")).toEqual({
      kind: "http",
      method: "GET",
      label: "/a/{param}",
      qualifier: null,
    });
    expect(parseContractId("code::@scope/pkg::fn")).toEqual({
      kind: "code",
      method: null,
      label: "fn",
      qualifier: "@scope/pkg",
    });
    expect(parseContractId("data::users").label).toBe("users");
    expect(parseContractId("plain").label).toBe("plain");
  });
});

describe("groupOrphanProviders", () => {
  it("counts every provider but keeps only a sample of rows", () => {
    const groups = groupOrphanProviders(
      [orphan("db", "data", 1), orphan("db", "data", 2), orphan("api", "http", 3)],
      1,
    );
    expect(groups.map((g) => [g.repo, g.contractType, g.count, g.rows.length])).toEqual([
      ["db", "data", 2, 1],
      ["api", "http", 1, 1],
    ]);
  });
});
