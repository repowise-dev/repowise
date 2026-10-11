import * as React from "react";
import { ProportionBar } from "../shared/proportion-bar";

/**
 * Language mix as a share bar plus a key.
 *
 * Languages are named in the key, so they take the share steps like every
 * other proportion rather than a hue each: a colour per language is only worth
 * its cost with a fixed legend the reader learns once, and there is none.
 */
export function LanguageBar({
  distribution,
  maxShown = 5,
}: {
  /** language → file count. */
  distribution: Record<string, number>;
  maxShown?: number;
}) {
  return (
    <ProportionBar
      label="Files by language"
      maxSegments={maxShown}
      othersLabel={() => "Other"}
      segments={Object.entries(distribution).map(([name, count]) => ({
        key: name,
        label: name,
        value: count,
      }))}
    />
  );
}
