"use client";

import React from "react";
import { usePathname } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import type { ReactNode } from "react";
import { cn } from "@repowise-dev/ui/lib/cn";

interface PageTransitionProps {
  children: ReactNode;
}

export function PageTransition({ children }: PageTransitionProps) {
  const pathname = usePathname();
  const isInternallyScrolledWorkspace = /\/repos\/[^/]+\/(?:chat|docs)\/?$/.test(pathname);

  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={pathname}
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -4 }}
        transition={{ duration: 0.15, ease: "easeOut" }}
        // `flex-1`, not `h-full`. `main` is a flex column, so this claims the
        // space left over after the route layout's banners instead of 100% of
        // `main` regardless of them. Most pages deliberately keep the default
        // `min-height: auto`, which lets a page taller than the viewport
        // grow and scroll `main`. Chat and the Docs reader are bounded
        // workspaces with their own scroll regions, so they opt out.
        className={cn("flex-1", isInternallyScrolledWorkspace && "min-h-0 overflow-hidden")}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}
