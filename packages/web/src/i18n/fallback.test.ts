import { describe, expect, it } from "vitest";
import { createTranslator } from "next-intl";
import { withFallback } from "./fallback";
import en from "../../messages/en.json";
import zhCN from "../../messages/zh-CN.json";

/**
 * The runtime contract behind the maintainer's request (2): a key a locale
 * catalog has not reached yet must render the *English* string, never the raw
 * key path and never a `MISSING_MESSAGE` throw.
 *
 * This drives the production path — the exported `withFallback` the request
 * config calls, then next-intl's own `createTranslator` — so breaking either
 * side turns this red.
 */
type Translator = (key: string) => string;

function translatorFor(catalog: Record<string, unknown>): Translator {
  return createTranslator({
    locale: "zh-CN",
    messages: withFallback(en, catalog),
  }) as unknown as Translator;
}

function withoutSkipToContent(): Record<string, unknown> {
  const partial = JSON.parse(JSON.stringify(zhCN));
  delete partial.shell.skipToContent;
  return partial;
}

describe("English fallback for a lagging locale catalog", () => {
  it("renders the English string for a key zh-CN has not translated", () => {
    expect(translatorFor(withoutSkipToContent())("shell.skipToContent")).toBe(
      en.shell.skipToContent,
    );
  });

  it("never renders the raw key path for a key only en.json defines", () => {
    const out = translatorFor(withoutSkipToContent())("shell.skipToContent");
    expect(out).not.toBe("shell.skipToContent");
    expect(out).not.toContain("MISSING_MESSAGE");
  });

  it("keeps a translated key translated (fallback does not clobber it)", () => {
    expect(translatorFor(zhCN)("shell.skipToContent")).toBe(
      zhCN.shell.skipToContent,
    );
  });
});
