// @vitest-environment jsdom

import { StrictMode } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReleaseMoment } from "./release-moment";

const meta = vi.hoisted(() => ({ server_version: "0.57.0", highlights: [] as unknown[] }));

vi.mock("@/lib/hooks/use-meta-version", () => ({
  useMetaVersion: () => ({ meta }),
  useChangelog: () => ({ entries: [], error: null, isLoading: false }),
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

beforeEach(() => window.localStorage.clear());
afterEach(cleanup);

describe("ReleaseMoment", () => {
  it("opens What's new once after a minor upgrade, even under StrictMode", () => {
    window.localStorage.setItem("repowise:last-seen-server-version", "0.56.0");
    render(
      <StrictMode>
        <ReleaseMoment />
      </StrictMode>,
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("opens on load for a browser that used repowise before the key existed", () => {
    window.localStorage.setItem("repowise:locale", "en");
    render(<ReleaseMoment />);
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("stays quiet on a first visit", () => {
    render(<ReleaseMoment />);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(window.localStorage.getItem("repowise:last-seen-server-version")).toBe("0.57.0");
  });

  it("shows the quiet note after a patch upgrade", () => {
    meta.server_version = "0.57.1";
    window.localStorage.setItem("repowise:last-seen-server-version", "0.57.0");
    render(<ReleaseMoment />);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("See what changed")).toBeTruthy();
    meta.server_version = "0.57.0";
  });
});
