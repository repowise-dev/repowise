/**
 * What a Bash result that `repowise distill` shortened says about itself:
 * read from the omission marker distill writes at the end of its output, and
 * from the test runner's own summary in what was kept. Pure; nothing fetched.
 */

/** The one marker `core/distill` renders (markers.py `_MARKER_TEMPLATE`). */
const MARKER = /^\[repowise#([0-9a-f]{12}): (\d+) lines omitted \(~(\d+) tokens\); restore: repowise expand \1\]$/;

export interface Squeeze {
  /** Lines the command printed. */
  originalLines: number;
  /** Lines Claude received, the marker aside. */
  keptLines: number;
  /** Tokens distill held back, as its marker estimates them. */
  omittedTokens: number;
  /** Failures and errors as the test runner counted them in what was kept; zero when it reported none. */
  failed: number;
  errors: number;
  /** Omission refs, in order; the first is the one to expand. */
  refs: string[];
}

interface Marker {
  ref: string;
  lines: number;
  tokens: number;
}

function markerOf(line: string): Marker | null {
  const m = MARKER.exec(line.trim());
  return m === null ? null : { ref: m[1]!, lines: Number(m[2]), tokens: Number(m[3]) };
}

const isBlank = (line: string) => line.trim() === "";

/**
 * Splits off the markers distill wrote: the trailing run of marker lines
 * (blank lines between them allowed). A marker anywhere else is text the
 * command printed, and draws nothing.
 */
function trailingMarkers(lines: string[]): { kept: string[]; markers: Marker[] } {
  let end = lines.length;
  const markers: Marker[] = [];
  while (end > 0) {
    const line = lines[end - 1]!;
    const marker = isBlank(line) ? null : markerOf(line);
    if (!isBlank(line) && marker === null) break;
    if (marker !== null) markers.unshift(marker);
    end--;
  }
  return { kept: lines.slice(0, end), markers };
}

// Runner summaries, in their own words.
const PYTEST_SUMMARY = /^=+ (.*\b(?:passed|failed|error|errors|skipped)\b.*) =+$/;
const PYTEST_FAILED = /\b(\d+) failed\b/;
const PYTEST_ERRORS = /\b(\d+) errors?\b/;
const JEST_SUMMARY = /^\s*Tests?:\s.*?\b(\d+) failed\b/;
const CARGO_SUMMARY = /^test result: \w+\. \d+ passed; (\d+) failed;/;
/** Top-level only: an indented `--- FAIL:` is a subtest of one already counted. */
const GO_FAIL = /^--- FAIL:/;

function pytestCounts(line: string): { failed: number; errors: number } | null {
  const m = PYTEST_SUMMARY.exec(line);
  if (m === null) return null;
  return { failed: Number(PYTEST_FAILED.exec(m[1]!)?.[1] ?? 0), errors: Number(PYTEST_ERRORS.exec(m[1]!)?.[1] ?? 0) };
}

/** Failures the runner itself reported in the kept text. */
export function runnerFailures(kept: string[]): { failed: number; errors: number } {
  let cargo = 0;
  let go = 0;
  for (const line of kept) {
    const pytest = pytestCounts(line);
    if (pytest !== null) return pytest;
    const jest = JEST_SUMMARY.exec(line);
    if (jest !== null) return { failed: Number(jest[1]), errors: 0 };
    cargo += Number(CARGO_SUMMARY.exec(line)?.[1] ?? 0);
    if (GO_FAIL.test(line)) go++;
  }
  return { failed: cargo + go, errors: 0 };
}

/** The squeeze a distilled output carries, or null when distill did not write a complete marker at its end. */
export function parseSqueeze(output: string): Squeeze | null {
  const { kept, markers } = trailingMarkers(output.split(/\r?\n/));
  if (markers.length === 0) return null;
  const refs = new Set<string>();
  let lines = 0;
  let tokens = 0;
  for (const m of markers) {
    if (refs.has(m.ref)) continue;
    refs.add(m.ref);
    lines += m.lines;
    tokens += m.tokens;
  }
  return {
    originalLines: kept.length + lines,
    keptLines: kept.length,
    omittedTokens: tokens,
    ...runnerFailures(kept),
    refs: [...refs],
  };
}

/** What a Bash result carries: `{stdout}` once it ran, or the text the model read when it errored. */
export function bashText(output: unknown): string | null {
  if (typeof output === "string") return output;
  const stdout = (output as { stdout?: unknown } | null)?.stdout;
  return typeof stdout === "string" ? stdout : null;
}
