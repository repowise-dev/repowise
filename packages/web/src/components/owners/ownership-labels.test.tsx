// @vitest-environment jsdom

import { renderHook } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import type { OwnershipLabels } from "@repowise-dev/ui/owners/labels";
import { DEFAULT_OWNERSHIP_LABELS } from "@repowise-dev/ui/owners/labels";
import enMessages from "../../../messages/en.json";
import zhMessages from "../../../messages/zh-CN.json";
import { useOwnershipLabels } from "./ownership-labels";

/** Every label rendered with sample arguments: strings as they are, functions called. */
function renderAll(labels: OwnershipLabels): Record<string, string> {
  const sample: Record<string, unknown[]> = {
    resultSentence: [{ names: "Ana", files: 3, threshold: "50%", codeShare: "9%", severe: 1, kept: 2 }],
  };
  return Object.fromEntries(
    Object.entries(labels).map(([key, value]) => {
      if (typeof value === "string") return [key, value];
      const args = sample[key] ?? Array.from({ length: value.length }, (_, i) => (i === 0 ? 2 : i % 2 ? 3 : "Ana"));
      return [key, String((value as (...a: unknown[]) => unknown)(...args))];
    }),
  );
}

function labelsFor(locale: string, messages: Record<string, unknown>): OwnershipLabels {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <NextIntlClientProvider locale={locale} messages={messages}>
      {children}
    </NextIntlClientProvider>
  );
  return renderHook(() => useOwnershipLabels(), { wrapper }).result.current;
}

describe("useOwnershipLabels", () => {
  it("fills every label the ui defines", () => {
    const labels = labelsFor("en", enMessages);
    expect(Object.keys(labels).sort()).toEqual(Object.keys(DEFAULT_OWNERSHIP_LABELS).sort());
  });

  it("formats every English message without leftover placeholders", () => {
    for (const [key, text] of Object.entries(renderAll(labelsFor("en", enMessages)))) {
      expect(text, key).not.toMatch(/[{}]|views\.contributors/);
    }
  });

  it("formats every zh-CN message in Chinese", () => {
    const zh = renderAll(labelsFor("zh-CN", zhMessages));
    for (const [key, text] of Object.entries(zh)) {
      expect(text, key).not.toMatch(/[{}]|views\.contributors/);
    }
    expect(zh.everyone).toBe("所有人");
    expect(zh.coverageLabel).toBe("知识覆盖率");
    expect(zh.showAllPeople).not.toMatch(/Show all/);
  });
});
