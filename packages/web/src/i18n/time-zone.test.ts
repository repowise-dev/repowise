// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { getDisplayTimeZone, setDisplayTimeZone } from "@repowise-dev/ui/lib/format";
import {
  DETECTED_TIME_ZONE_COOKIE,
  TIME_ZONE_COOKIE,
  readCookie,
  resolveViewerTimeZone,
  timeZoneCookie,
} from "./time-zone";

const jar = new Map<string, string>();
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (jar.has(name) ? { value: jar.get(name) } : undefined) }),
}));
vi.mock("next-intl/server", () => ({ getRequestConfig: (fn: unknown) => fn }));

describe("resolveViewerTimeZone", () => {
  it("prefers an explicit zone over the detected one", () => {
    expect(resolveViewerTimeZone("Asia/Tokyo", "Europe/Berlin")).toEqual({
      preference: "Asia/Tokyo",
      zone: "Asia/Tokyo",
    });
  });

  it("follows the browser on auto or no cookie", () => {
    expect(resolveViewerTimeZone("auto", "Europe/Berlin")).toEqual({
      preference: "auto",
      zone: "Europe/Berlin",
    });
    expect(resolveViewerTimeZone(undefined, "Europe/Berlin").zone).toBe("Europe/Berlin");
  });

  it("renders UTC before the browser has reported, and ignores junk", () => {
    expect(resolveViewerTimeZone(undefined, undefined)).toEqual({ preference: "auto", zone: "UTC" });
    expect(resolveViewerTimeZone("<script>", "Not/AZone")).toEqual({ preference: "auto", zone: "UTC" });
  });
});

describe("time zone cookies", () => {
  afterEach(() => {
    document.cookie = `${TIME_ZONE_COOKIE}=; max-age=0; path=/`;
  });

  it("writes a value readCookie reads back unchanged", () => {
    document.cookie = timeZoneCookie(TIME_ZONE_COOKIE, "Etc/GMT+5");
    expect(readCookie(TIME_ZONE_COOKIE)).toBe("Etc/GMT+5");
    expect(timeZoneCookie(TIME_ZONE_COOKIE, "UTC")).toContain("path=/");
  });
});

describe("request config", () => {
  afterEach(() => {
    jar.clear();
    setDisplayTimeZone("UTC");
  });

  async function config() {
    const { default: getConfig } = await import("./request");
    return (getConfig as unknown as (p: { requestLocale: Promise<string | undefined> }) => Promise<{
      timeZone: string;
    }>)({ requestLocale: Promise.resolve(undefined) });
  }

  it("hands next-intl and format.ts the cookie's zone", async () => {
    jar.set(TIME_ZONE_COOKIE, "America/Chicago");
    expect((await config()).timeZone).toBe("America/Chicago");
    expect(getDisplayTimeZone()).toBe("America/Chicago");
  });

  it("uses the detected zone on auto, and UTC with no cookies", async () => {
    jar.set(DETECTED_TIME_ZONE_COOKIE, "Australia/Sydney");
    expect((await config()).timeZone).toBe("Australia/Sydney");
    jar.clear();
    expect((await config()).timeZone).toBe("UTC");
  });
});
