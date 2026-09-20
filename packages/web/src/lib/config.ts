/**
 * Settings persisted in localStorage.
 * Keys are prefixed with "repowise_".
 * All helpers are safe to call in SSR — they return defaults when window is undefined.
 */

const KEYS = {
  apiKey: "repowise_api_key",
  apiUrl: "repowise_api_url",
  provider: "repowise_default_provider",
  model: "repowise_default_model",
  embedder: "repowise_embedder",
  weekend: "repowise_weekend",
  chatDockHidden: "repowise_chat_dock_hidden",
  chatAskControlsHidden: "repowise_chat_ask_controls_hidden",
  chatSelectionAskHidden: "repowise_chat_selection_ask_hidden",
  chatHintSeen: "repowise_chat_hint_seen",
} as const;

function read(key: string): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(key) ?? "";
}

function write(key: string, value: string): void {
  if (typeof window === "undefined") return;
  if (value) {
    localStorage.setItem(key, value);
  } else {
    localStorage.removeItem(key);
  }
}

export const config = {
  getApiKey: () => read(KEYS.apiKey),
  setApiKey: (v: string) => write(KEYS.apiKey, v),

  getApiUrl: () => read(KEYS.apiUrl),
  setApiUrl: (v: string) => write(KEYS.apiUrl, v),

  getProvider: () => read(KEYS.provider) || "litellm",
  setProvider: (v: string) => write(KEYS.provider, v),

  getModel: () => read(KEYS.model),
  setModel: (v: string) => write(KEYS.model, v),

  getEmbedder: () => read(KEYS.embedder) || "mock",
  setEmbedder: (v: string) => write(KEYS.embedder, v),

  /** Weekend-days preset id; "" means unset, which resolves to Sat/Sun. */
  getWeekend: () => read(KEYS.weekend),
  setWeekend: (v: string) => write(KEYS.weekend, v),

  /** Whether the "Ask Repowise" dock is hidden. Lives here rather than in the
   *  dock's own persisted state, which is keyed per repo AND per conversation
   *  and would therefore forget the choice on the next new chat. Default is
   *  shown, so an unset value reads as false. */
  getChatDockHidden: () => read(KEYS.chatDockHidden) === "1",
  setChatDockHidden: (v: boolean) => write(KEYS.chatDockHidden, v ? "1" : ""),

  /** Whether the per-object "Ask about this" controls are hidden. Same
   *  default-shown encoding as the dock. */
  getChatAskControlsHidden: () => read(KEYS.chatAskControlsHidden) === "1",
  setChatAskControlsHidden: (v: boolean) =>
    write(KEYS.chatAskControlsHidden, v ? "1" : ""),

  /** Whether the control that appears beside a text selection is hidden. */
  getChatSelectionAskHidden: () => read(KEYS.chatSelectionAskHidden) === "1",
  setChatSelectionAskHidden: (v: boolean) =>
    write(KEYS.chatSelectionAskHidden, v ? "1" : ""),

  /** Whether the pill has already introduced itself once in this browser. */
  getChatHintSeen: () => read(KEYS.chatHintSeen) === "1",
  setChatHintSeen: () => write(KEYS.chatHintSeen, "1"),
};

/** Fires when any chat affordance's visibility changes in this tab.
 *  `localStorage` only notifies OTHER tabs via `storage`, so without this the
 *  affordance and the settings toggle would not agree until a reload. One
 *  event for all three: every reader of one reads the others too, because
 *  hiding the dock hides the page controls with it. */
export const CHAT_DOCK_VISIBILITY_EVENT = "repowise:chat-dock-visibility";

function announce(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(CHAT_DOCK_VISIBILITY_EVENT));
  }
}

export function setChatDockHidden(hidden: boolean): void {
  config.setChatDockHidden(hidden);
  announce();
}

export function setChatAskControlsHidden(hidden: boolean): void {
  config.setChatAskControlsHidden(hidden);
  announce();
}

export function setChatSelectionAskHidden(hidden: boolean): void {
  config.setChatSelectionAskHidden(hidden);
  announce();
}
