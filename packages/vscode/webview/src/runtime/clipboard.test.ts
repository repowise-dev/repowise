import { afterEach, describe, expect, it, vi } from "vitest";
import { installClipboardBridge } from "./clipboard";
import type { WebviewHost } from "./rpc";

const original = Object.getOwnPropertyDescriptor(navigator, "clipboard");

afterEach(() => {
  if (original) Object.defineProperty(navigator, "clipboard", original);
  else delete (navigator as { clipboard?: unknown }).clipboard;
});

function hostWith(copyText = vi.fn()) {
  return { host: { copyText } as unknown as WebviewHost, copyText };
}

describe("installClipboardBridge", () => {
  it("keeps the browser's copy when it works", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const { host, copyText } = hostWith();
    installClipboardBridge(host);

    await navigator.clipboard.writeText("prompt");
    expect(writeText).toHaveBeenCalledWith("prompt");
    expect(copyText).not.toHaveBeenCalled();
  });

  it("copies through the editor when the frame refuses", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const { host, copyText } = hostWith();
    installClipboardBridge(host);

    await expect(navigator.clipboard.writeText("prompt")).resolves.toBeUndefined();
    expect(copyText).toHaveBeenCalledWith("prompt");
  });

  it("copies through the editor when there is no clipboard API", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    const { host, copyText } = hostWith();
    installClipboardBridge(host);

    await navigator.clipboard.writeText("prompt");
    expect(copyText).toHaveBeenCalledWith("prompt");
  });
});
