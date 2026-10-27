// A synthetic repository, deterministic (a fixed-seed generator, no
// randomness between runs), for scale cases the recorded Django feed cannot
// cover: n files over a few levels of folders, sizes skewed like real code
// (many small files, a few large). Labelled synthetic wherever it is used.
import type { MapFile } from "../src/views/map";

/** A small linear congruential generator: the same sequence for the same seed. */
function lcg(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1_664_525) + 1_013_904_223) >>> 0;
    return s / 0x1_0000_0000;
  };
}

const TOP = ["src", "lib", "tests", "docs", "tools", "packages", "services", "web"];

/** `n` files, largest first (as the health-map feed orders them), with the repository's total. */
export function syntheticTree(n: number, seed = 7): { files: MapFile[]; total: number } {
  const next = lcg(seed);
  const files: MapFile[] = [];
  for (let i = 0; i < n; i++) {
    const top = TOP[Math.floor(next() * TOP.length)] as string;
    const mid = `m${Math.floor(next() * 24)}`;
    const leaf = `d${Math.floor(next() * 12)}`;
    const nloc = Math.max(1, Math.round(20 / (next() + 0.02) ** 1.3));
    files.push({ file_path: `${top}/${mid}/${leaf}/f${i}.py`, score: Math.round(next() * 100) / 10, nloc });
  }
  files.sort((a, b) => b.nloc - a.nloc || (a.file_path < b.file_path ? -1 : 1));
  return { files, total: n };
}
