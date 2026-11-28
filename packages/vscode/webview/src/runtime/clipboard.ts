/**
 * Copies from shared components reach the clipboard inside a webview.
 *
 * Shared UI copies with `navigator.clipboard.writeText` (the agent prompt
 * button and dialog, code blocks, file paths). A webview's iframe can refuse
 * that, and the component would then say "Couldn't copy" for text the editor
 * can copy fine. This wraps the call once per page: the browser's copy first,
 * and when it throws or is missing, the editor's clipboard through the host.
 */

import type { WebviewHost } from "./rpc";

export function installClipboardBridge(host: WebviewHost): void {
  const clipboard = (navigator as { clipboard?: Clipboard }).clipboard;
  const native = clipboard?.writeText?.bind(clipboard);
  const writeText = async (text: string): Promise<void> => {
    if (native) {
      try {
        await native(text);
        return;
      } catch {
        // Refused in this frame: fall through to the editor's clipboard.
      }
    }
    host.copyText(text);
  };
  try {
    if (clipboard) Object.defineProperty(clipboard, "writeText", { value: writeText, configurable: true });
    else Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  } catch {
    // A locked-down navigator keeps its own behaviour.
  }
}
