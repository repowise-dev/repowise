// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DocsTree } from "@repowise-dev/ui/docs/docs-tree";
import { DocsReader } from "@repowise-dev/ui/docs/docs-reader";
import { DEFAULT_PERSONA } from "@repowise-dev/ui/docs/reader-persona";
import type { DocPageSummary } from "@repowise-dev/types/docs";
import zhMessages from "../../../messages/zh-CN.json";
import enMessages from "../../../messages/en.json";
import { NoProseNotice } from "./bulk-generate-dialogs";
import { useDocsReaderCopy, useDocsTreeCopy } from "./use-docs-copy";

function page(overrides: Partial<DocPageSummary>): DocPageSummary {
  return {
    id: "file_page:a.py",
    repository_id: "r1",
    page_type: "file_page",
    title: "a.py",
    target_path: "a.py",
    source_hash: "h",
    model_name: "",
    provider_name: "template",
    input_tokens: 0,
    output_tokens: 0,
    cached_tokens: 0,
    generation_level: 1,
    version: 1,
    confidence: 1,
    freshness_status: "fresh",
    human_notes: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

const PAGES = [
  page({}),
  page({
    id: "symbol_spotlight:a.py::run",
    page_type: "symbol_spotlight",
    target_path: "a.py::run",
    title: "run",
    parent_page_id: "file_page:a.py",
  }),
  page({ id: "module_page:core", page_type: "module_page", target_path: "core", title: "Module: core" }),
];

function TranslatedTree() {
  return <DocsTree pages={PAGES} selectedPageId={null} onSelectPage={() => {}} copy={useDocsTreeCopy()} />;
}

function TranslatedMissingPage() {
  return (
    <DocsReader
      page={null}
      repoId="r1"
      persona={DEFAULT_PERSONA}
      sidebarOpen={false}
      buildPageHref={(id) => `?page=${id}`}
      missingPageId="file_page:src/gone.py"
      LinkComponent="a"
      copy={useDocsReaderCopy()}
    />
  );
}

function inLocale(locale: string, messages: Record<string, unknown>, ui: React.ReactNode) {
  return render(
    <NextIntlClientProvider locale={locale} messages={messages}>
      {ui}
    </NextIntlClientProvider>,
  );
}

describe("docs copy", () => {
  afterEach(cleanup);

  it("renders the tree's own wording in zh-CN, counts included", () => {
    inLocale("zh-CN", zhMessages, <TranslatedTree />);
    expect(screen.getByPlaceholderText("搜索文档...")).toBeTruthy();
    expect(screen.getByText("代码参考")).toBeTruthy();
    expect(screen.getByText("1 个文件 · 1 个符号")).toBeTruthy();
    expect(screen.getByRole("button", { name: "1 个未撰写" })).toBeTruthy();
  });

  it("keeps the English plurals when the locale is en", () => {
    inLocale("en", enMessages, <TranslatedTree />);
    expect(screen.getByText("3 pages")).toBeTruthy();
    expect(screen.getByText("1 file · 1 symbol")).toBeTruthy();
  });

  it("names the missing page inside the translated sentence", () => {
    inLocale("zh-CN", zhMessages, <TranslatedMissingPage />);
    expect(screen.getByText("这里还没有页面")).toBeTruthy();
    expect(screen.getByText("src/gone.py")).toBeTruthy();
  });

  it("explains a keyless index once, with the write action beside it", () => {
    const onClick = vi.fn();
    inLocale("en", enMessages, <NoProseNotice pages={PAGES} count={83} running={false} onClick={onClick} />);
    expect(screen.getByText(/has not written the prose for 83 pages yet/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Write with AI" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("stays silent once any page carries model prose", () => {
    const written = page({ id: "module_page:x", page_type: "module_page", provider_name: "openai" });
    const { container } = inLocale(
      "en",
      enMessages,
      <NoProseNotice pages={[...PAGES, written]} count={1} running={false} onClick={vi.fn()} />,
    );
    expect(container.textContent).toBe("");
  });
});
