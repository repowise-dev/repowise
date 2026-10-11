import { describe, expect, it } from "vitest";
import { bashText, parseSqueeze, runnerFailures, type Squeeze } from "../src/model/squeeze";
import { squeezeLine } from "../src/views/copy";
import { BAR_CELLS, squeezeBar, squeezeView } from "../src/views/squeeze";
import { fixture, golden } from "./fake-host";

// Real `repowise distill <cmd>` output, recorded on the requests index (CRLF,
// as the Windows console wrote it; local paths in the pytest run rewritten to
// a neutral root). The raw counts were checked against the commands run
// without distill: `git log -n 300` prints 2,949 lines, `git ls-files` 130,
// `grep -rn "def " src` 268. git-diff and ruff are cut to their first kept
// lines and the verbatim marker, so their counts are the cut file's.
const CASES = [
  { file: "pytest-failing.txt", ref: "21ece412acd8", original: 123, kept: 59, tokens: 2337, failed: 4, errors: 0 },
  { file: "git-log.txt", ref: "eed993b23bc3", original: 2949, kept: 21, tokens: 25947, failed: 0, errors: 0 },
  { file: "git-diff.txt", ref: "8ba29e69b831", original: 3397, kept: 6, tokens: 29929, failed: 0, errors: 0 },
  { file: "git-ls-files.txt", ref: "41d94b327885", original: 130, kept: 24, tokens: 396, failed: 0, errors: 0 },
  { file: "grep.txt", ref: "4d5207d9f4ad", original: 268, kept: 47, tokens: 4304, failed: 0, errors: 0 },
  { file: "ruff.txt", ref: "4f688e17124a", original: 11968, kept: 6, tokens: 103796, failed: 0, errors: 0 },
];

const marker = (ref: string, lines: number, tokens = 50) =>
  `[repowise#${ref}: ${lines} lines omitted (~${tokens} tokens); restore: repowise expand ${ref}]`;
const A = "aaaaaaaaaaaa";
const B = "bbbbbbbbbbbb";

describe("parseSqueeze over real distill output", () => {
  it.each(CASES)("$file", ({ file, ref, original, kept, tokens, failed, errors }) => {
    expect(parseSqueeze(fixture(`distill/${file}`))).toEqual({
      originalLines: original,
      keptLines: kept,
      omittedTokens: tokens,
      failed,
      errors,
      refs: [ref],
    });
  });

  it("reads the marker core/distill writes (golden line shared with the Python test)", () => {
    const line = golden("distill-marker.txt").trim();
    expect(parseSqueeze(`kept one\nkept two\n\n${line}\n`)).toMatchObject({
      originalLines: 66,
      keptLines: 2,
      omittedTokens: 2337,
      refs: ["21ece412acd8"],
    });
  });

  it("reads the same output with LF line ends", () => {
    const lf = fixture("distill/git-log.txt").replace(/\r\n/g, "\n");
    expect(parseSqueeze(lf)).toMatchObject({ originalLines: 2949, keptLines: 21 });
  });

  it("is null for output distill left alone", () => {
    expect(parseSqueeze(fixture("distill/git-status-undistilled.txt"))).toBeNull();
  });

  it("draws nothing for a marker the command itself printed, anywhere but the end", () => {
    expect(parseSqueeze(`${marker(A, 10)}\nmore output after it\n`)).toBeNull();
    expect(parseSqueeze(`see ${marker(A, 10)} above\n`)).toBeNull();
  });

  it("ignores a marker without counts, or whose restore ref disagrees with its own", () => {
    expect(parseSqueeze(`x\n\n[repowise#${A}: ~6.1k tokens omitted]\n`)).toBeNull();
    expect(parseSqueeze(`[repowise#${A}: 64 lines omitted (~2337 tokens); restore: repowise expand ${B}]`)).toBeNull();
  });

  it("adds up the trailing markers, each ref once", () => {
    expect(parseSqueeze(`one\ntwo\n\n${marker(A, 10)}\n\n${marker(B, 5, 20)}\n${marker(A, 10)}\n`)).toEqual({
      originalLines: 17,
      keptLines: 2,
      omittedTokens: 70,
      failed: 0,
      errors: 0,
      refs: [A, B],
    });
  });
});

describe("runnerFailures", () => {
  it("reads pytest's own summary, failures and errors apart", () => {
    expect(runnerFailures(["==== 4 failed, 1 error, 60 passed in 1.2s ===="])).toEqual({ failed: 4, errors: 1 });
    expect(runnerFailures(["==== 2 errors in 0.3s ===="])).toEqual({ failed: 0, errors: 2 });
    expect(runnerFailures(["==== 60 passed in 1.2s ===="])).toEqual({ failed: 0, errors: 0 });
  });

  it("reads jest and vitest summaries", () => {
    expect(runnerFailures(["Tests:       2 failed, 30 passed, 32 total"])).toEqual({ failed: 2, errors: 0 });
  });

  it("sums every cargo result line", () => {
    expect(
      runnerFailures([
        "test result: FAILED. 8 passed; 3 failed; 0 ignored",
        "test result: FAILED. 1 passed; 2 failed; 0 ignored",
        "test result: ok. 5 passed; 0 failed; 0 ignored",
      ]),
    ).toEqual({ failed: 5, errors: 0 });
  });

  it("counts go's top-level failures, not their subtests", () => {
    expect(runnerFailures(["--- FAIL: TestA (0.00s)", "    --- FAIL: TestA/sub (0.00s)", "--- FAIL: TestB (0.00s)", "FAIL"])).toEqual({
      failed: 2,
      errors: 0,
    });
  });

  it("takes nothing from text that only mentions failing", () => {
    expect(runnerFailures(["abc123  Fix 2 failed uploads"])).toEqual({ failed: 0, errors: 0 });
  });
});

describe("bashText", () => {
  it("reads stdout from a Bash result, or the text an errored call carried", () => {
    expect(bashText({ stdout: "out", stderr: "" })).toBe("out");
    expect(bashText("Exit code 1\nout")).toBe("Exit code 1\nout");
    expect(bashText({ stderr: "x" })).toBeNull();
    expect(bashText(null)).toBeNull();
  });
});

describe("squeeze row", () => {
  const s: Squeeze = { originalLines: 1204, keptLines: 38, omittedTokens: 9400, failed: 4, errors: 0, refs: ["9c1e4b2a7f01"] };

  it("says lines before and after, tokens omitted, failures as the runner said them, and the restore command", () => {
    expect(squeezeLine(s)).toBe("1,204 lines → 38 · ~9,400 tokens omitted · 4 failed · repowise expand 9c1e4b2a7f01");
    expect(squeezeLine({ ...s, failed: 4, errors: 1 })).toContain("· 4 failed, 1 error ·");
    expect(squeezeLine({ ...s, failed: 0, errors: 2 })).toContain("· 2 errors ·");
    expect(squeezeLine({ ...s, failed: 0, omittedTokens: 0, refs: ["9c1e4b2a7f01", A] })).toBe(
      "1,204 lines → 38 · repowise expand 9c1e4b2a7f01 (+1 more)",
    );
  });

  it("draws the kept share as a bar, never empty", () => {
    expect(squeezeBar(s)).toBe(`█${"░".repeat(BAR_CELLS - 1)}`);
    expect(squeezeBar({ ...s, keptLines: 602 })).toBe("██████░░░░░░");
    expect(squeezeBar({ ...s, keptLines: 1204 })).toBe("█".repeat(BAR_CELLS));
  });

  it("is one dim row, cut at the edge rather than wrapped", () => {
    expect(squeezeView(s)).toEqual({
      type: "Text",
      props: { dimColor: true, wrap: "truncate-end" },
      children: [`█${"░".repeat(BAR_CELLS - 1)} ${squeezeLine(s)}`],
    });
  });
});
