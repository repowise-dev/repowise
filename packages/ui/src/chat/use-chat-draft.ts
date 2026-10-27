"use client";

import { useEffect, useState } from "react";

/** Portable per-conversation draft persistence for page and hosted surfaces. */
export function useChatDraft(storageKey: string) {
  const [state, setState] = useState({ storageKey, value: "" });

  useEffect(() => {
    let value = "";
    try {
      value = window.localStorage.getItem(storageKey) ?? "";
    } catch {}
    setState({ storageKey, value });
  }, [storageKey]);

  const visible = state.storageKey === storageKey ? state.value : "";
  const setValue = (value: string) => {
    setState({ storageKey, value });
    try {
      if (value) window.localStorage.setItem(storageKey, value);
      else window.localStorage.removeItem(storageKey);
    } catch {}
  };
  return [visible, setValue] as const;
}
