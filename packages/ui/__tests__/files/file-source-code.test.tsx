import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { FileSourceCode } from "../../src/files/file-source-code.js";
import { asLineNumber } from "../../src/files/file-page-tabs.js";

const HTML =
  '<pre><code>' +
  '<span class="line" data-line="1">a = 1</span>' +
  '<span class="line" data-line="2" data-covered="y">b = 2</span>' +
  '<span class="line" data-line="3">c = 3</span>' +
  "</code></pre>";

describe("FileSourceCode", () => {
  const scrollIntoView = vi.fn();
  beforeEach(() => {
    // jsdom does not implement scrolling.
    Element.prototype.scrollIntoView = scrollIntoView;
  });
  afterEach(() => {
    scrollIntoView.mockReset();
  });

  it("marks the linked line and scrolls it into view", () => {
    const { container } = render(<FileSourceCode html={HTML} highlightLine={3} />);
    const row = container.querySelector('[data-line="3"]');
    expect(row?.hasAttribute("data-highlighted")).toBe(true);
    expect(row?.getAttribute("aria-current")).toBe("location");
    expect(container.querySelectorAll("[data-highlighted]")).toHaveLength(1);
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView.mock.contexts[0]).toBe(row);
  });

  it.each([0, -4, 2.5, Number.NaN, 99])("ignores line %s", (line) => {
    const { container } = render(<FileSourceCode html={HTML} highlightLine={line} />);
    expect(container.querySelector("[data-highlighted]")).toBeNull();
    expect(container.querySelector("[aria-current]")).toBeNull();
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("moves the mark when the line changes", () => {
    const { container, rerender } = render(<FileSourceCode html={HTML} highlightLine={1} />);
    rerender(<FileSourceCode html={HTML} highlightLine={2} />);
    const marked = container.querySelectorAll("[data-highlighted]");
    expect(marked).toHaveLength(1);
    expect(marked[0]?.getAttribute("data-line")).toBe("2");
    expect(container.querySelectorAll("[aria-current]")).toHaveLength(1);
  });

  it("renders the source untouched without a line", () => {
    const { container } = render(<FileSourceCode html={HTML} filePath="src/a.py" />);
    expect(container.querySelectorAll(".line")).toHaveLength(3);
    expect(container.querySelector("[data-highlighted]")).toBeNull();
    expect(container.querySelector('[data-chat-selection-path="src/a.py"]')).not.toBeNull();
    expect(scrollIntoView).not.toHaveBeenCalled();
  });
});

describe("asLineNumber", () => {
  it("reads a plain positive integer", () => {
    expect(asLineNumber("194")).toBe(194);
    expect(asLineNumber("1")).toBe(1);
  });

  it.each([undefined, "", "0", "-1", "abc", "12abc", "1.5", "1e3", " 7", "99999999", ["3", "4"]])(
    "ignores %j",
    (value) => {
      expect(asLineNumber(value)).toBeUndefined();
    },
  );
});
