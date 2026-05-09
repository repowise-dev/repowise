"use client";

import { useState, useCallback } from "react";

export function useExpandedModules() {
  const [expandedModules, setExpandedModules] = useState<Set<string>>(new Set());

  const toggleModule = useCallback((moduleId: string) => {
    setExpandedModules((prev) => {
      const next = new Set(prev);
      if (next.has(moduleId)) next.delete(moduleId);
      else next.add(moduleId);
      return next;
    });
  }, []);

  const collapseAll = useCallback(() => {
    setExpandedModules(new Set());
  }, []);

  const expandAll = useCallback((moduleIds: string[]) => {
    setExpandedModules(new Set(moduleIds));
  }, []);

  return { expandedModules, toggleModule, collapseAll, expandAll };
}
