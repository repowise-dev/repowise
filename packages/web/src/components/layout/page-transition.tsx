"use client";

import { usePathname } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import type { ReactNode } from "react";

interface PageTransitionProps {
  children: ReactNode;
}

export function PageTransition({ children }: PageTransitionProps) {
  const pathname = usePathname();

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
        // `main` regardless of them. Deliberately no `min-h-0`: the default
        // `min-height: auto` keeps a page taller than the viewport able to
        // grow and scroll `main`, which is what every document page relies on.
        className="flex-1"
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}
