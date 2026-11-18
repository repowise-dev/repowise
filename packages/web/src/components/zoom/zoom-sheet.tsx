"use client";

/**
 * The detail sheet's frame on the canvas's right edge: `w-80` at `right-3`
 * (the canvas pans clear of exactly `ZOOM_SHEET_INSET_PX`), Escape closes it,
 * and its bottom stops above the chat dock's pill while the dock shows.
 */

import type { ReactNode } from "react";
import { CHAT_DOCK_PILL_REACH } from "@repowise-dev/ui/chat";
import { useTranslations } from "next-intl";
import { useChatDockHidden } from "@/components/chat/use-chat-dock-hidden";

/** The sheet's width plus its gap from the canvas edge (`w-80` + `right-3`). */
export const ZOOM_SHEET_INSET_PX = 320 + 12;

/**
 * The sheet's height beside the dock's pill at the viewport's bottom right.
 * The canvas ends `--page-pad` above the viewport bottom (the page frame's
 * padding), so the pill reaches `CHAT_DOCK_PILL_REACH - --page-pad` into the
 * canvas; 0.5rem of air above it. Ceiling: a viewport so wide the canvas ends
 * left of the pill still reserves the strip; measure the two rects if that
 * matters.
 */
const MAX_H_BESIDE_DOCK = `calc(100% - 0.75rem - max(0.75rem, ${CHAT_DOCK_PILL_REACH} + 0.5rem - var(--page-pad, 1.5rem)))`;

export function ZoomSheet({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  const t = useTranslations("zoom.sheet");
  const dockHidden = useChatDockHidden();
  // Focus stays on the canvas so arrow keys keep stepping between siblings.
  return (
    <div
      role="complementary"
      aria-label={t("label")}
      className="absolute right-3 top-3 z-20 flex max-h-[calc(100%-1.5rem)] w-80 max-w-[calc(100%-1.5rem)] flex-col rounded-lg shadow-lg motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-right-2"
      style={dockHidden ? undefined : { maxHeight: MAX_H_BESIDE_DOCK }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      {children}
    </div>
  );
}
