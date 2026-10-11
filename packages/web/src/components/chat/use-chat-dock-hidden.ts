"use client";

import { useEffect, useState } from "react";
import { CHAT_DOCK_VISIBILITY_EVENT, config } from "@/lib/config";

/**
 * Which chat affordances this browser shows, kept in step with the settings
 * toggles without a reload.
 *
 * Starts at the defaults and reads stored values after mount rather than during
 * render: `localStorage` does not exist on the server, so seeding from it
 * directly would make the server and the first client render disagree and
 * flash the dock away on hydration.
 *
 * Two listeners, because they cover different cases. `storage` fires only in
 * OTHER tabs, so it keeps a second window honest; the custom event covers the
 * tab that made the change, where `storage` stays silent.
 */
export interface ChatAffordances {
  dockHidden: boolean;
  /** Per-object "Ask about this" controls. */
  askControlsEnabled: boolean;
  /** The control that appears beside a text selection. */
  selectionAskEnabled: boolean;
}

const DEFAULTS: ChatAffordances = {
  dockHidden: false,
  askControlsEnabled: true,
  selectionAskEnabled: true,
};

function readAffordances(): ChatAffordances {
  // Hiding the dock hides the page controls with it: they all lead to the same
  // place, and a control that opens something the reader has switched off is a
  // dead path.
  const dockHidden = config.getChatDockHidden();
  return {
    dockHidden,
    askControlsEnabled: !dockHidden && !config.getChatAskControlsHidden(),
    selectionAskEnabled: !dockHidden && !config.getChatSelectionAskHidden(),
  };
}

export function useChatAffordances(): ChatAffordances {
  const [state, setState] = useState<ChatAffordances>(DEFAULTS);

  useEffect(() => {
    const sync = () =>
      setState((current) => {
        const next = readAffordances();
        return current.dockHidden === next.dockHidden &&
          current.askControlsEnabled === next.askControlsEnabled &&
          current.selectionAskEnabled === next.selectionAskEnabled
          ? current
          : next;
      });
    sync();
    window.addEventListener(CHAT_DOCK_VISIBILITY_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(CHAT_DOCK_VISIBILITY_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  return state;
}

export function useChatDockHidden(): boolean {
  return useChatAffordances().dockHidden;
}
