import { useMemo } from "react";

/** Contour rings drawn from a commit sha, so each repository and commit gets its own. */
export function contourPaths(sha: string | null | undefined): string[] {
  let seed = Number.parseInt((sha ?? "").slice(0, 8), 16) >>> 0 || 0x9e3779b9;
  const rnd = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 4294967296;
  };
  const phase = [rnd() * 6, rnd() * 6, rnd() * 6] as const;
  const paths: string[] = [];
  for (let r = 18; r < 300; r += 14) {
    let d = "";
    for (let k = 0; k <= 72; k++) {
      const a = (k / 72) * Math.PI * 2;
      const w =
        1 +
        0.12 * Math.sin(3 * a + phase[0] + r / 60) +
        0.08 * Math.sin(5 * a + phase[1]) +
        0.05 * Math.sin(2 * a + phase[2] + r / 90);
      const x = 420 + Math.cos(a) * r * w * 1.25;
      const y = 110 + Math.sin(a) * r * w * 0.8;
      d += `${k ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
    }
    paths.push(`${d}Z`);
  }
  return paths;
}

/** The header's faint backdrop. Decoration only: hidden from assistive tech, print and export. */
export function ContourPattern({ sha }: { sha: string | null | undefined }) {
  const paths = useMemo(() => contourPaths(sha), [sha]);
  return (
    <svg
      viewBox="0 0 560 260"
      aria-hidden
      data-decoration
      className="pointer-events-none absolute right-[-40px] top-0 -z-10 h-[260px] w-[min(560px,90%)] text-[var(--color-accent-primary)] opacity-[0.16] [mask-image:linear-gradient(to_left,black_30%,transparent)] print:hidden"
    >
      {paths.map((d, i) => (
        <path key={i} d={d} fill="none" stroke="currentColor" strokeWidth={1} />
      ))}
    </svg>
  );
}
