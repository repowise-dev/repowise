/**
 * middleware.ts proxies /api/* to the API through Next's rewrite proxy, whose
 * default 30 s idle timeout hung up on model calls the API went on to finish
 * (and bill). The raised timeout is what keeps those replies reaching the page.
 */

import { describe, expect, it } from "vitest";
import nextConfig from "../next.config";

describe("next.config", () => {
  it("lets a proxied API call stay silent longer than one model call", () => {
    expect(nextConfig.experimental?.proxyTimeout).toBeGreaterThanOrEqual(5 * 60 * 1000);
  });
});
