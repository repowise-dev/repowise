// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";
import zhMessages from "../../../messages/zh-CN.json";
import { BulkWriteButton } from "./bulk-generate-dialogs";

describe("BulkWriteButton", () => {
  afterEach(cleanup);
  it("names how many pages it writes and opens the flow", () => {
    const onClick = vi.fn();
    render(<BulkWriteButton count={12} running={false} onClick={onClick} />);
    const button = screen.getByRole("button", { name: "Write 12 pages with AI" });
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(button.className).toContain("--color-model");
  });

  it("uses the singular for one page", () => {
    render(<BulkWriteButton count={1} running={false} onClick={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Write 1 page with AI" })).toBeTruthy();
  });

  it("is disabled while a run is going", () => {
    render(<BulkWriteButton count={3} running onClick={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Write 3 pages with AI" })).toHaveProperty("disabled", true);
  });

  it("renders nothing once no page is left to write", () => {
    const { container } = render(<BulkWriteButton count={0} running={false} onClick={vi.fn()} />);
    expect(container.innerHTML).toBe("");
  });

  it("carries the zh-CN copy", () => {
    render(
      <NextIntlClientProvider locale="zh-CN" messages={zhMessages}>
        <BulkWriteButton count={12} running={false} onClick={vi.fn()} />
      </NextIntlClientProvider>,
    );
    expect(screen.getByRole("button", { name: "用 AI 撰写 12 个页面" })).toBeTruthy();
  });
});
