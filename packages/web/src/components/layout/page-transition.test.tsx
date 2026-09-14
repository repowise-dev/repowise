// @vitest-environment jsdom

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PageTransition } from "./page-transition";

const navigation = vi.hoisted(() => ({ pathname: "/repos/r1/chat" }));

vi.mock("next/navigation", () => ({ usePathname: () => navigation.pathname }));
vi.mock("framer-motion", () => ({
  AnimatePresence: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  motion: {
    div: ({ children, initial: _initial, animate: _animate, exit: _exit, transition: _transition, ...props }: React.HTMLAttributes<HTMLDivElement> & { initial?: unknown; animate?: unknown; exit?: unknown; transition?: unknown }) => (
      <div data-testid="transition" {...props}>{children}</div>
    ),
  },
}));

afterEach(cleanup);

describe("PageTransition", () => {
  it("locks the chat workspace to the available viewport", () => {
    render(<PageTransition>Chat</PageTransition>);
    expect(screen.getByTestId("transition").className).toContain("min-h-0");
    expect(screen.getByTestId("transition").className).toContain("overflow-hidden");
  });

  it("locks the Docs reader so its tree and article scroll independently", () => {
    navigation.pathname = "/repos/r1/docs";
    render(<PageTransition>Docs</PageTransition>);
    expect(screen.getByTestId("transition").className).toContain("min-h-0");
    expect(screen.getByTestId("transition").className).toContain("overflow-hidden");
  });

  it("keeps ordinary document routes eligible for main-page scrolling", () => {
    navigation.pathname = "/repos/r1/files";
    render(<PageTransition>Files</PageTransition>);
    expect(screen.getByTestId("transition").className).not.toContain("overflow-hidden");
  });
});
