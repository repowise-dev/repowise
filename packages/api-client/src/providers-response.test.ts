import { beforeEach, describe, expect, it } from "vitest";

import { configureApiClient } from "./client";
import { addProviderKey, getProviderModels, removeProviderKey } from "./providers";

describe("provider key responses", () => {
  beforeEach(() => {
    configureApiClient({
      baseUrl: "http://test.local",
      fetch: async () => new Response(null, { status: 204 }),
    });
  });

  it("accepts the empty successful save response", async () => {
    await expect(
      addProviderKey("openrouter", "not-a-real-key"),
    ).resolves.toBeUndefined();
  });

  it("accepts the empty successful delete response", async () => {
    await expect(removeProviderKey("openrouter")).resolves.toBeUndefined();
  });
});

describe("provider models request", () => {
  it("sends the repo, the probe URL and refresh as query parameters", async () => {
    const urls: string[] = [];
    configureApiClient({
      baseUrl: "http://test.local",
      fetch: async (input) => {
        urls.push(String(input));
        return Response.json({ models: [], source: "catalog", default_model: "m" });
      },
    });

    await getProviderModels("openai", {
      repoId: "r1",
      baseUrl: "http://localhost:1234/v1",
      refresh: true,
    });
    await getProviderModels("openai");

    expect(urls).toEqual([
      "http://test.local/api/providers/openai/models?repo_id=r1&base_url=http%3A%2F%2Flocalhost%3A1234%2Fv1&refresh=true",
      "http://test.local/api/providers/openai/models",
    ]);
  });
});
