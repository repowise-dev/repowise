import { describe, it, expect } from "vitest";
import {
  MAX_PROMPT_ROWS,
  buildContractAiPrompt,
  buildOrphanProvidersAiPrompt,
  buildUnmatchedConsumersAiPrompt,
  type ContractPromptLink,
} from "../../src/workspace/contract-ai-prompt.js";
import type { ContractEntry } from "../../src/workspace/contract-facts.js";

function contract(overrides: Partial<ContractEntry> = {}): ContractEntry {
  return {
    contract_id: "http::GET::/users/{param}",
    contract_type: "http",
    role: "provider",
    repo: "users-api",
    file_path: "src/routes/users.py",
    symbol_name: "fastapi::get_user",
    confidence: 0.85,
    service: "users",
    line: 42,
    symbol_id: null,
    meta: { method: "GET", path: "/users/{param}", framework: "fastapi", extraction_layer: "index" },
    ...overrides,
  };
}

const LINK: ContractPromptLink = {
  match_type: "exact",
  confidence: 0.65,
  provider_repo: "users-api",
  provider_file: "src/routes/users.py",
  provider_symbol: "get_user",
  consumer_repo: "web",
  consumer_file: "src/api/users.ts",
  consumer_symbol: "fetchUser",
};

describe("buildContractAiPrompt", () => {
  it("carries the identity, location and how it was found", () => {
    const prompt = buildContractAiPrompt({ contract: contract(), links: [LINK] });
    expect(prompt).toContain("## Contract: GET /users/{param}");
    expect(prompt).toContain("`http::GET::/users/{param}` (HTTP, provider)");
    expect(prompt).toContain("`users-api` `src/routes/users.py:42`, service `users`");
    expect(prompt).toContain("Framework: `fastapi`");
    // The caller is named with its match basis.
    expect(prompt).toContain("`web` `src/api/users.ts` `fetchUser` (exact match, 65%)");
    expect(prompt).not.toContain("\u2014");
  });

  it("does not call an unlinked provider dead", () => {
    const prompt = buildContractAiPrompt({ contract: contract(), links: [] });
    expect(prompt).toContain("That is not proof it is unused");
    expect(prompt).toContain("Do not delete anything in this pass");
  });

  it("names the host and the next step for an external consumer", () => {
    const prompt = buildContractAiPrompt({
      contract: contract({ role: "consumer", meta: { host: "api.github.com" } }),
      links: [],
      unmatchedReason: "external_host",
    });
    expect(prompt).toContain("Recorded reason: `external_host`");
    expect(prompt).toContain("`api.github.com`");
    expect(prompt).toContain("is a third party");
  });

  it("asks for the intended endpoint when no provider exists", () => {
    const prompt = buildContractAiPrompt({
      contract: contract({ role: "consumer" }),
      links: [],
      unmatchedReason: "no_provider",
    });
    expect(prompt).toContain("Find the endpoint this call intends");
  });

  it("lists breaking changes when the report has them", () => {
    const prompt = buildContractAiPrompt({
      contract: contract(),
      links: [LINK],
      breakingChanges: [{ severity: "breaking", kind: "removed_field", detail: "id was removed" }],
    });
    expect(prompt).toContain("## Breaking changes in the latest update");
    expect(prompt).toContain("breaking: `removed_field`: id was removed");
  });

  it("steers the MCP flavor to repowise tools", () => {
    const prompt = buildContractAiPrompt({ contract: contract(), links: [], flavor: "claude-code-mcp" });
    expect(prompt).toContain("get_blast_radius");
    expect(prompt).toContain("search_codebase");
  });
});

describe("buildUnmatchedConsumersAiPrompt", () => {
  const row = (i: number) => ({
    repo: "web",
    file_path: `src/api/r${i}.ts`,
    contract_id: `http::GET::/r/${i}`,
    contract_type: "http",
  });

  it("states the count, the reason and every listed call", () => {
    const prompt = buildUnmatchedConsumersAiPrompt({ reason: "no_provider", rows: [row(1), row(2)] });
    expect(prompt).toContain("## 2 client calls that resolve to no provider: no provider found");
    expect(prompt).toContain("`no_provider`");
    expect(prompt).toContain("1. `http::GET::/r/1` in `web` `src/api/r1.ts` (HTTP)");
    expect(prompt).toContain("renamed (fix the call)");
  });

  it("caps the list and says how many it left out", () => {
    const rows = Array.from({ length: MAX_PROMPT_ROWS + 3 }, (_, i) => row(i));
    const prompt = buildUnmatchedConsumersAiPrompt({ reason: "no_provider", rows });
    expect(prompt).toContain(`## ${MAX_PROMPT_ROWS + 3} client calls`);
    expect(prompt).toContain("...and 3 more calls not listed");
    expect(prompt).not.toContain(`/r/${MAX_PROMPT_ROWS}\``);
  });

  it("tells the agent there is little to do for external hosts", () => {
    const prompt = buildUnmatchedConsumersAiPrompt({ reason: "external_host", rows: [row(1)] });
    expect(prompt).toContain("## 1 client call that resolves to no provider");
    expect(prompt).toContain("For the rest there is nothing to fix");
  });
});

describe("buildOrphanProvidersAiPrompt", () => {
  it("names the repo and type, lists providers and forbids deletion", () => {
    const prompt = buildOrphanProvidersAiPrompt({
      repo: "backend",
      contractType: "data",
      rows: [
        { repo: "backend", file_path: "db/schema.sql", contract_id: "data::audit_log", contract_type: "data" },
      ],
    });
    expect(prompt).toContain("## 1 table in `backend` with no caller in this workspace");
    expect(prompt).toContain("1. `data::audit_log` in `db/schema.sql`");
    expect(prompt).toContain("That is not proof they are unused");
    expect(prompt).toContain("Never remove a provider on the strength of this list alone");
  });
});
