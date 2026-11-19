// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { DETECTED_TIME_ZONE_COOKIE, TIME_ZONE_COOKIE, readCookie } from "@/i18n/time-zone";
import { TimeZoneSync } from "./time-zone-sync";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
vi.mock("@repowise-dev/ui/lib/format", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@repowise-dev/ui/lib/format")>()),
  browserTimeZone: () => "Europe/Berlin",
}));

function clear(name: string) {
  document.cookie = `${name}=; max-age=0; path=/`;
}

describe("TimeZoneSync", () => {
  afterEach(() => {
    refresh.mockClear();
    clear(TIME_ZONE_COOKIE);
    clear(DETECTED_TIME_ZONE_COOKIE);
  });

  it("reports the browser zone and refreshes once when the server guessed wrong", () => {
    render(<TimeZoneSync zone="UTC" />);
    expect(readCookie(DETECTED_TIME_ZONE_COOKIE)).toBe("Europe/Berlin");
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("does nothing when the server already rendered the browser zone", () => {
    render(<TimeZoneSync zone="Europe/Berlin" />);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("leaves an explicit choice alone", () => {
    document.cookie = `${TIME_ZONE_COOKIE}=Asia/Tokyo; path=/`;
    render(<TimeZoneSync zone="Asia/Tokyo" />);
    expect(readCookie(DETECTED_TIME_ZONE_COOKIE)).toBeUndefined();
    expect(refresh).not.toHaveBeenCalled();
  });
});
