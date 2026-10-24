"use client";

import { useCallback, useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

/**
 * Held-out groups brought back into the map, in `?include=` (e.g.
 * `test,build`). Replaced rather than pushed: it is a view setting, not a
 * place to go back to. Every section under the map reads the same scope.
 */
export function useIncludeParam() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const raw = searchParams.get("include") ?? "";
  const include = useMemo(
    () => raw.split(",").map((o) => o.trim().toLowerCase()).filter(Boolean),
    [raw],
  );
  const setInclude = useCallback(
    (next: string[]) => {
      const sp = new URLSearchParams(searchParams.toString());
      if (next.length) sp.set("include", next.join(","));
      else sp.delete("include");
      const qs = sp.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [router, pathname, searchParams],
  );
  return [include, setInclude] as const;
}
