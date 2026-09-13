import { beforeEach, describe, expect, it } from "vitest";

import { configureApiClient } from "./client";
import { addProviderKey, removeProviderKey } from "./providers";

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
