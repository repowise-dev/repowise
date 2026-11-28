import { afterEach, describe, expect, it } from "vitest";

import { configureApiClient, createAdapterFetch, type ApiRequest } from "./client";
import {
  createRuntimeEvidenceApi,
  getEvidenceTests,
  getMeasured,
  getMeasuredFunctions,
} from "./runtime-evidence";

function serve() {
  const sent: string[] = [];
  configureApiClient({
    baseUrl: "http://127.0.0.1:7337",
    fetch: createAdapterFetch(async (url) => {
      sent.push(url.replace("http://127.0.0.1:7337", ""));
      return { status: 200, ok: true, headers: {}, text: "{}" };
    }),
  });
  return sent;
}

describe("runtime evidence requests", () => {
  afterEach(() => configureApiClient({ baseUrl: "" }));

  it("sends the run and limit under the server's names", async () => {
    const sent = serve();
    await getMeasured("repo", { runId: "r1", limit: 0 });
    expect(sent).toEqual(["/api/repos/repo/evidence/measured?run_id=r1&limit=0"]);
  });

  it("asks for one function or one file", async () => {
    const sent = serve();
    await getMeasuredFunctions("repo", { symbolId: "src/a.py::f" });
    await getMeasuredFunctions("repo", { file: "src/a.py" });
    expect(sent).toEqual([
      "/api/repos/repo/evidence/functions?symbol_id=src%2Fa.py%3A%3Af",
      "/api/repos/repo/evidence/functions?file=src%2Fa.py",
    ]);
  });

  it("asks for the slowest tests with a limit", async () => {
    const sent = serve();
    await getEvidenceTests("repo", { limit: 10 });
    await getEvidenceTests("repo");
    expect(sent).toEqual(["/api/repos/repo/evidence/tests?limit=10", "/api/repos/repo/evidence/tests"]);
  });

  it("builds a host's client over its own request and routes", async () => {
    const paths: string[] = [];
    const request: ApiRequest = async <T,>(path: string) => {
      paths.push(path);
      return {} as T;
    };
    const api = createRuntimeEvidenceApi(request, (id) => `/snapshots/${id}/evidence`);
    await api.getEvidenceStatus("s1");
    await api.listEvidenceRuns("s1");
    await api.getMeasured("s1", { limit: 5 });
    expect(paths).toEqual([
      "/snapshots/s1/evidence/status",
      "/snapshots/s1/evidence/runs",
      "/snapshots/s1/evidence/measured?limit=5",
    ]);
  });
});
