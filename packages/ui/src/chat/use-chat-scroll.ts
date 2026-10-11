"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const BOTTOM_THRESHOLD = 48;

function isAtBottom(viewport: HTMLElement) {
  return viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight <= BOTTOM_THRESHOLD;
}

/**
 * Scroll intent for a growing transcript.
 *
 * Live output never opts itself into follow mode. Sending reveals the new turn
 * once, while Jump to latest is the explicit action that follows subsequent
 * output. Native scroll anchoring remains enabled so markdown reflow above the
 * viewport does not displace the sentence a reader is on.
 */
export function useChatScroll() {
  const viewportNodeRef = useRef<HTMLDivElement | null>(null);
  const contentNodeRef = useRef<HTMLDivElement | null>(null);
  const [viewportNode, setViewportNode] = useState<HTMLDivElement | null>(null);
  const [contentNode, setContentNode] = useState<HTMLDivElement | null>(null);
  const viewportRef = useCallback((node: HTMLDivElement | null) => {
    viewportNodeRef.current = node;
    setViewportNode(node);
  }, []);
  const contentRef = useCallback((node: HTMLDivElement | null) => {
    contentNodeRef.current = node;
    setContentNode(node);
  }, []);
  const followLiveRef = useRef(false);
  const revealFrameRef = useRef<number | null>(null);
  const [hasContentBelow, setHasContentBelow] = useState(false);
  const [isFollowingLive, setIsFollowingLive] = useState(false);

  const updateContentBelow = useCallback(() => {
    const viewport = viewportNodeRef.current;
    if (!viewport) return;
    setHasContentBelow(!isAtBottom(viewport));
  }, []);

  const stopFollowing = useCallback(() => {
    followLiveRef.current = false;
    setIsFollowingLive(false);
  }, []);

  useEffect(() => {
    const viewport = viewportNode;
    if (!viewport) return;

    const onScroll = () => {
      if (!isAtBottom(viewport)) stopFollowing();
      updateContentBelow();
    };
    viewport.addEventListener("scroll", onScroll, { passive: true });
    return () => viewport.removeEventListener("scroll", onScroll);
  }, [stopFollowing, updateContentBelow, viewportNode]);

  useEffect(() => {
    const content = contentNode;
    if (!content || typeof ResizeObserver === "undefined") return;

    const observer = new ResizeObserver(() => {
      const viewport = viewportNodeRef.current;
      if (!viewport) return;
      if (followLiveRef.current) {
        viewport.scrollTop = viewport.scrollHeight;
      }
      updateContentBelow();
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [contentNode, updateContentBelow]);

  useEffect(
    () => () => {
      if (revealFrameRef.current !== null) {
        window.cancelAnimationFrame(revealFrameRef.current);
      }
    },
    [],
  );

  const revealNewTurn = useCallback(() => {
    const content = contentNodeRef.current;
    const existingUserTurns = content?.querySelectorAll('[data-chat-role="user"]').length ?? 0;
    stopFollowing();

    let attempts = 0;
    const reveal = () => {
      const viewport = viewportNodeRef.current;
      const currentContent = contentNodeRef.current;
      if (!viewport || !currentContent) return;
      const userTurns = currentContent.querySelectorAll<HTMLElement>(
        '[data-chat-role="user"]',
      );
      if (userTurns.length > existingUserTurns) {
        const turn = userTurns[userTurns.length - 1];
        if (turn) viewport.scrollTop = Math.max(0, turn.offsetTop - 16);
        updateContentBelow();
        return;
      }
      attempts += 1;
      if (attempts < 5) revealFrameRef.current = window.requestAnimationFrame(reveal);
    };
    revealFrameRef.current = window.requestAnimationFrame(reveal);
  }, [stopFollowing, updateContentBelow]);

  const jumpToLatest = useCallback(() => {
    const viewport = viewportNodeRef.current;
    if (!viewport) return;
    followLiveRef.current = true;
    setIsFollowingLive(true);
    viewport.scrollTop = viewport.scrollHeight;
    setHasContentBelow(false);
  }, []);

  return {
    viewportRef,
    contentRef,
    hasContentBelow,
    isFollowingLive,
    revealNewTurn,
    jumpToLatest,
  };
}
