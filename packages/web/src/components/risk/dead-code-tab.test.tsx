// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import type { DeadCodeSummary } from "@repowise-dev/types/dead-code";
import { DeadCodeLede } from "@repowise-dev/ui/dead-code";
import zhMessages from "../../../messages/zh-CN.json";
import { useLedeLabels } from "./dead-code-tab";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const SUMMARY: DeadCodeSummary = {
  total_findings: 1420,
  confidence_summary: {},
  deletable_lines: 4321,
  safe_to_delete_count: 12,
  total_lines: 91234,
  by_kind: {},
  analyzed_at: null,
};

function Lede({ summary }: { summary: DeadCodeSummary }) {
  return <DeadCodeLede summary={summary} labels={useLedeLabels()} />;
}

const NONE_READY = { ...SUMMARY, total_findings: 17, total_lines: 2029, deletable_lines: 0, safe_to_delete_count: 0 };

describe("dead-code lede copy", () => {
  it("reads the English catalog", () => {
    const { container } = render(<Lede summary={SUMMARY} />);
    expect(container.querySelector("p:has(> span)")?.textContent).toBe(
      "12 findings are deletion-ready. 1,408 more are flagged for review.",
    );
    expect(screen.getByRole("button", { name: "What Deletion-ready means" })).toBeTruthy();
  });

  it("says plainly in English when nothing is deletion-ready", () => {
    const { container } = render(<Lede summary={NONE_READY} />);
    expect(container.querySelector("p:has(> span)")?.textContent).toBe(
      "Nothing is deletion-ready right now. 17 findings across 2,029 lines are flagged for review.",
    );
  });

  it("reads the zh-CN catalog", () => {
    const { container } = render(
      <NextIntlClientProvider locale="zh-CN" messages={zhMessages}>
        <Lede summary={SUMMARY} />
        <Lede summary={NONE_READY} />
      </NextIntlClientProvider>,
    );
    expect([...container.querySelectorAll("p:has(> span)")].map((p) => p.textContent)).toEqual([
      "12 项发现可删除。另有 1,408 项已标记，待审查。",
      "目前没有可删除的代码。17 项发现（共 2,029 行）已标记，待审查。",
    ]);
  });
});
