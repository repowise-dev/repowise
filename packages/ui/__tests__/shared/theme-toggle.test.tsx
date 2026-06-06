import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ThemeProvider } from "next-themes";
import type { ReactNode } from "react";
import { ThemeToggle } from "../../src/shared/theme-toggle.js";

// Mirrors the consumer mount in packages/web (defaultTheme="light",
// enableSystem={false}, two explicit themes). The migration effect in
// ThemeToggle keys off the next-themes storage, so the tests exercise the
// real provider against jsdom localStorage.
function Harness({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="light"
      enableSystem={false}
      themes={["light", "dark"]}
    >
      {children}
    </ThemeProvider>
  );
}

const STORAGE_KEY = "theme"; // next-themes default key

describe("ThemeToggle theme default + persistence", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.className = "";
    // next-themes reads matchMedia even with enableSystem=false.
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("defaults a fresh visitor (empty storage) to light", async () => {
    render(
      <Harness>
        <ThemeToggle />
      </Harness>,
    );

    await waitFor(() => {
      expect(screen.getByRole("radio", { name: "Light" })).toHaveAttribute(
        "aria-checked",
        "true",
      );
    });
    expect(screen.getByRole("radio", { name: "Dark" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    // Migration must not write anything for a fresh visitor.
    expect(localStorage.getItem(STORAGE_KEY)).not.toBe("dark");
  });

  it("keeps an explicit stored dark choice (never clobbered by the default)", async () => {
    localStorage.setItem(STORAGE_KEY, "dark");
    render(
      <Harness>
        <ThemeToggle />
      </Harness>,
    );

    await waitFor(() => {
      expect(screen.getByRole("radio", { name: "Dark" })).toHaveAttribute(
        "aria-checked",
        "true",
      );
    });
    expect(localStorage.getItem(STORAGE_KEY)).toBe("dark");
  });

  it("keeps an explicit stored light choice", async () => {
    localStorage.setItem(STORAGE_KEY, "light");
    render(
      <Harness>
        <ThemeToggle />
      </Harness>,
    );

    await waitFor(() => {
      expect(screen.getByRole("radio", { name: "Light" })).toHaveAttribute(
        "aria-checked",
        "true",
      );
    });
    expect(localStorage.getItem(STORAGE_KEY)).toBe("light");
  });

  it("migrates a stale persisted 'system' value to the light default", async () => {
    localStorage.setItem(STORAGE_KEY, "system");
    render(
      <Harness>
        <ThemeToggle />
      </Harness>,
    );

    await waitFor(() => {
      expect(localStorage.getItem(STORAGE_KEY)).toBe("light");
    });
    expect(screen.getByRole("radio", { name: "Light" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
});
