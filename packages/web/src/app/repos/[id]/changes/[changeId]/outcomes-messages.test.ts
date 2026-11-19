import { describe, expect, it } from "vitest";
import en from "../../../../../../messages/en.json";
import zhCN from "../../../../../../messages/zh-CN.json";

const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

// The catalogs nest other groups (categories); only the flat outcome strings are read.
const enMessages = en.views.changes as unknown as Record<string, string>;
const zhMessages = zhCN.views.changes as unknown as Record<string, string>;

describe("Outcomes copy", () => {
  const enKeys = Object.keys(enMessages).filter(
    (k) => k.startsWith("outcomes") || k === "detailOutcomes",
  );

  it("has every key in both catalogs with the same placeholders", () => {
    expect(enKeys.length).toBeGreaterThan(0);
    for (const key of enKeys) {
      expect(zhMessages[key], key).toBeTypeOf("string");
      expect(placeholders(zhMessages[key]), key).toEqual(placeholders(enMessages[key]));
    }
  });

  it("never says a change caused anything", () => {
    for (const key of enKeys) {
      const text = enMessages[key];
      expect(text.replace("not what caused them", "")).not.toMatch(/\bcaused\b/i);
    }
  });
});
