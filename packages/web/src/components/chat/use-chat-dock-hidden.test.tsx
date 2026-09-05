// @vitest-environment jsdom

import React from "react";
import { render, screen, act, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { config, setChatDockHidden } from "@/lib/config";
import { useChatDockHidden } from "./use-chat-dock-hidden";

function Probe() {
  return <span>{useChatDockHidden() ? "hidden" : "shown"}</span>;
}

describe("useChatDockHidden", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  // This config wires no RTL auto-cleanup, so without this each test inherits
  // the previous test's mounted probe and every query finds two of everything.
  afterEach(cleanup);

  it("shows the dock when nothing has been stored", () => {
    render(<Probe />);
    expect(screen.getByText("shown")).toBeTruthy();
  });

  it("reads a stored preference after mount", () => {
    config.setChatDockHidden(true);
    render(<Probe />);
    expect(screen.getByText("hidden")).toBeTruthy();
  });

  it("reacts to a change made in this tab", () => {
    // The bug this guards: `localStorage` fires `storage` only in OTHER tabs,
    // so without the custom event the settings toggle and the dock disagree
    // until a reload.
    render(<Probe />);
    expect(screen.getByText("shown")).toBeTruthy();

    act(() => setChatDockHidden(true));
    expect(screen.getByText("hidden")).toBeTruthy();

    act(() => setChatDockHidden(false));
    expect(screen.getByText("shown")).toBeTruthy();
  });

  it("reacts to a change made in another tab", () => {
    render(<Probe />);

    act(() => {
      config.setChatDockHidden(true);
      window.dispatchEvent(new StorageEvent("storage"));
    });

    expect(screen.getByText("hidden")).toBeTruthy();
  });

  it("survives a round trip through storage", () => {
    // An unset key must read as shown, not as an empty-string surprise.
    expect(config.getChatDockHidden()).toBe(false);
    config.setChatDockHidden(true);
    expect(config.getChatDockHidden()).toBe(true);
    config.setChatDockHidden(false);
    expect(config.getChatDockHidden()).toBe(false);
  });
});
