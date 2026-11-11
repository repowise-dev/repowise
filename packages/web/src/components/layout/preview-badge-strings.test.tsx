// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";
import { PreviewBadge } from "@repowise-dev/ui/health/preview-badge";
import zhMessages from "../../../messages/zh-CN.json";
import { PreviewBadgeStrings } from "./preview-badge-strings";

const NOTE = "preview, measured 22% precision on 55 judged plans, 2026-10-15";

describe("PreviewBadgeStrings", () => {
  it("gives the badge the English catalog copy", () => {
    render(
      <PreviewBadgeStrings>
        <PreviewBadge note={NOTE} />
      </PreviewBadgeStrings>,
    );
    expect(screen.getByRole("button", { name: `Preview: ${NOTE}` }).textContent).toBe("Preview");
  });

  it("gives the badge the zh-CN catalog copy", () => {
    render(
      <NextIntlClientProvider locale="zh-CN" messages={zhMessages}>
        <PreviewBadgeStrings>
          <PreviewBadge note={NOTE} />
        </PreviewBadgeStrings>
      </NextIntlClientProvider>,
    );
    expect(screen.getByRole("button", { name: `预览：${NOTE}` }).textContent).toBe("预览");
  });
});
