import { describe, expect, it } from "vitest";
import { getLanguageFromPath } from "../../src/lib/language-from-path";

describe("getLanguageFromPath", () => {
  it("detects languages from file paths", () => {
    expect(getLanguageFromPath("src/app.py")).toBe("python");
    expect(getLanguageFromPath("src/index.ts")).toBe("typescript");
    expect(getLanguageFromPath("src/App.tsx")).toBe("typescript");
    expect(getLanguageFromPath("main.go")).toBe("go");
    expect(getLanguageFromPath("lib.rs")).toBe("rust");
    expect(getLanguageFromPath("Makefile")).toBe("text");
    expect(getLanguageFromPath("styles.css")).toBe("css");
  });
});
