import { beforeEach, describe, expect, it, vi } from "vitest";

const apiGet = vi.fn();
vi.mock("./client", () => ({
  apiGet: (...args: unknown[]) => apiGet(...args),
}));

import { getEvidenceTests, getMeasured, getMeasuredFunctions } from "./runtime-evidence";

describe("runtime evidence requests", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiGet.mockResolvedValue({});
  });

  it("sends the run and limit under the server's names", async () => {
    await getMeasured("repo", { runId: "r1", limit: 0 });
    expect(apiGet).toHaveBeenCalledWith("/api/repos/repo/evidence/measured", {
      run_id: "r1",
      limit: 0,
    });
  });

  it("asks for one function or one file", async () => {
    await getMeasuredFunctions("repo", { symbolId: "src/a.py::f" });
    expect(apiGet).toHaveBeenLastCalledWith("/api/repos/repo/evidence/functions", {
      symbol_id: "src/a.py::f",
      file: undefined,
    });
    await getMeasuredFunctions("repo", { file: "src/a.py" });
    expect(apiGet).toHaveBeenLastCalledWith("/api/repos/repo/evidence/functions", {
      symbol_id: undefined,
      file: "src/a.py",
    });
  });

  it("asks for the slowest tests with a limit", async () => {
    await getEvidenceTests("repo", { limit: 10 });
    expect(apiGet).toHaveBeenCalledWith("/api/repos/repo/evidence/tests", { limit: 10 });
  });
});
