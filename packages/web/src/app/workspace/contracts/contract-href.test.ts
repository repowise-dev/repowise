import { describe, it, expect } from "vitest";
import { contractDetailHref } from "./contract-href";

describe("contractDetailHref", () => {
  it("carries all three parts of the identity", () => {
    const href = contractDetailHref({
      repo: "repowise",
      file_path: "packages/api-client/src/c4.ts",
      contract_id: "http::GET::/api/graph/{param}/c4/mermaid",
    });
    const params = new URLSearchParams(href.split("?")[1]);
    expect(href.startsWith("/workspace/contracts/detail?")).toBe(true);
    expect(params.get("repo")).toBe("repowise");
    expect(params.get("file")).toBe("packages/api-client/src/c4.ts");
    expect(params.get("id")).toBe("http::GET::/api/graph/{param}/c4/mermaid");
  });

  it("escapes the slashes and braces a contract id carries", () => {
    // `id` and `file` both hold characters a path segment would have to escape,
    // which is why the route takes them as query parameters.
    const href = contractDetailHref({
      repo: "backend",
      file_path: "app/routers/github_app.py",
      contract_id: "http::GET::/app/installations/{param}",
    });
    expect(href).not.toContain("{");
    expect(new URLSearchParams(href.split("?")[1]).get("id")).toBe(
      "http::GET::/app/installations/{param}",
    );
  });

  it("distinguishes two repos declaring the same contract id", () => {
    const id = "http::GET::/user";
    const a = contractDetailHref({ repo: "a", file_path: "x.ts", contract_id: id });
    const b = contractDetailHref({ repo: "b", file_path: "x.ts", contract_id: id });
    expect(a).not.toBe(b);
  });
});
