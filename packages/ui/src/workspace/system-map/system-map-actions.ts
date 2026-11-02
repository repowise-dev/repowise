/**
 * Verbs a card offers on the canvas (open or close a group or the tray, show
 * what a "+N more" card holds), handed down by context rather than through
 * node data, so a new callback never re-renders every card.
 */

import { createContext, useContext } from "react";

export interface SystemMapActions {
  /** Open a group id, or `TRAY_OPEN_TOKEN` for the tray, in place. */
  openContainer: (id: string) => void;
  closeContainer: (id: string) => void;
  /** Present when the host can list folded services (the list view). */
  showFolded?: ((serviceIds: readonly string[]) => void) | undefined;
}

const NOOP: SystemMapActions = { openContainer: () => {}, closeContainer: () => {} };

export const SystemMapActionsContext = createContext<SystemMapActions>(NOOP);

export function useSystemMapActions(): SystemMapActions {
  return useContext(SystemMapActionsContext);
}
