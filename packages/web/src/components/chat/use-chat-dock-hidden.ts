"use client";

import { useEffect, useState } from "react";
import { CHAT_DOCK_VISIBILITY_EVENT, config } from "@/lib/config";

/**
 * Whether the "Ask Repowise" dock is hidden, kept in step with the settings
 * toggle without a reload.
 *
 * Starts `false` and reads the stored value after mount rather than during
 * render: `localStorage` does not exist on the server, so seeding from it
 * directly would make the server and the first client render disagree and
 * flash the dock away on hydration.
 *
 * Two listeners, because they cover different cases. `storage` fires only in
 * OTHER tabs, so it keeps a second window honest; the custom event covers the
 * tab that made the change, where `storage` stays silent.
 */
export function useChatDockHidden(): boolean {
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    const sync = () => setHidden(config.getChatDockHidden());
    sync();
    window.addEventListener(CHAT_DOCK_VISIBILITY_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(CHAT_DOCK_VISIBILITY_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  return hidden;
}
