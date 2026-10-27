// @vitest-environment jsdom

import React from "react";
import { render, screen, act, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  config,
  setChatAskControlsHidden,
  setChatDockHidden,
  setChatSelectionAskHidden,
} from "@/lib/config";
import { useChatAffordances, useChatDockHidden } from "./use-chat-dock-hidden";

function Probe() {
  return <span>{useChatDockHidden() ? "hidden" : "shown"}</span>;
}

function AffordanceProbe() {
  const { dockHidden, askControlsEnabled, selectionAskEnabled } =
    useChatAffordances();
  return (
    <span>
      {[
        dockHidden ? "dock-hidden" : "dock-shown",
        askControlsEnabled ? "ask-on" : "ask-off",
        selectionAskEnabled ? "selection-on" : "selection-off",
      ].join(" ")}
    </span>
  );
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

describe("useChatAffordances", () => {
  beforeEach(() => localStorage.clear());
  afterEach(cleanup);

  it("defaults both page controls on", () => {
    render(<AffordanceProbe />);
    expect(screen.getByText("dock-shown ask-on selection-on")).toBeTruthy();
  });

  it("switches each control off on its own", () => {
    render(<AffordanceProbe />);

    act(() => setChatAskControlsHidden(true));
    expect(screen.getByText("dock-shown ask-off selection-on")).toBeTruthy();

    act(() => setChatSelectionAskHidden(true));
    expect(screen.getByText("dock-shown ask-off selection-off")).toBeTruthy();
  });

  it("hides both controls whenever the dock itself is hidden", () => {
    render(<AffordanceProbe />);
    act(() => setChatDockHidden(true));
    expect(screen.getByText("dock-hidden ask-off selection-off")).toBeTruthy();
  });

  it("restores a control's own choice when the dock comes back", () => {
    config.setChatAskControlsHidden(true);
    render(<AffordanceProbe />);

    act(() => setChatDockHidden(true));
    expect(screen.getByText("dock-hidden ask-off selection-off")).toBeTruthy();

    act(() => setChatDockHidden(false));
    expect(screen.getByText("dock-shown ask-off selection-on")).toBeTruthy();
  });

  it("stays off across reloads and across tabs", () => {
    // A reload is a fresh mount reading the same storage.
    config.setChatSelectionAskHidden(true);
    render(<AffordanceProbe />);
    expect(screen.getByText("dock-shown ask-on selection-off")).toBeTruthy();
    cleanup();

    render(<AffordanceProbe />);
    expect(screen.getByText("dock-shown ask-on selection-off")).toBeTruthy();

    act(() => {
      config.setChatAskControlsHidden(true);
      window.dispatchEvent(new StorageEvent("storage"));
    });
    expect(screen.getByText("dock-shown ask-off selection-off")).toBeTruthy();
  });
});
