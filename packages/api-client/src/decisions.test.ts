import { afterEach, describe, expect, it } from "vitest";

import { ApiClientError, configureApiClient, createAdapterFetch } from "./client";
import { patchDecision } from "./decisions";

function serve(status: number, body: unknown) {
  const sent: { url: string; body?: string }[] = [];
  configureApiClient({
    baseUrl: "http://127.0.0.1:7337",
    fetch: createAdapterFetch(async (url, init) => {
      sent.push({ url, ...(init.body !== undefined ? { body: init.body } : {}) });
      return { status, ok: status < 300, headers: {}, text: JSON.stringify(body) };
    }),
  });
  return sent;
}

describe("patchDecision", () => {
  afterEach(() => configureApiClient({ baseUrl: "" }));

  it("sends the reason with the confirm", async () => {
    const sent = serve(200, { id: "d1" });
    await patchDecision("r1", "d1", { status: "active", reason: "Keeps reads cheap." });
    expect(JSON.parse(sent[0]!.body!)).toEqual({ status: "active", reason: "Keeps reads cheap." });
  });

  it("keeps a refusal's blockers on the error", async () => {
    serve(400, {
      detail: {
        message: "This decision cannot be confirmed as it stands.",
        blockers: ["no evidence reference", 7],
      },
    });
    const err = await patchDecision("r1", "d1", { status: "active" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiClientError);
    expect((err as ApiClientError).blockers).toEqual(["no evidence reference"]);
    expect((err as ApiClientError).detail).toBe(
      "This decision cannot be confirmed as it stands. no evidence reference",
    );
  });
});
