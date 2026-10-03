import { describe, expect, it } from "vitest";
import { absoluteKey, absolutePath, fromToolCall, relativeTo } from "../src/model/events";
import { initialTrail, reduceTrail, type TrailAction } from "../src/model/trail";
import { layoutMap } from "../src/views/map";
import { litFromTrail } from "../src/model/story";
import { notOnMap, resolveLit } from "../src/views/overlay";
import { fixture } from "./fake-host";

/** Tool calls recorded from a headless Claude Code 2.1.288 run in a Django copy, its root renamed to C:/work/django (long strings cut). */
const recorded = JSON.parse(fixture("tool-calls.json")) as Record<string, { e: { tool: string; [k: string]: unknown }; r: unknown }>;
const django = { cwd: "C:/work/django", isWindows: true };

const win = { cwd: "C:\\Users\\Dev\\django", isWindows: true };
const posix = { cwd: "/home/dev/django", isWindows: false };
const ok = { result: {} };

describe("fromToolCall, per tool shape", () => {
  it("Read with a Windows absolute path: backslashes, drive letter and case normalized", () => {
    const e = { tool: "Read", tool_use_id: "toolu_1", file_path: "C:\\Users\\Dev\\django\\django\\db\\models\\Query.py" };
    expect(fromToolCall(e, ok, win)).toEqual({ type: "read", path: "c:/users/dev/django/django/db/models/query.py" });
  });

  it("a lowercase drive letter and forward slashes land on the same key", () => {
    const a = fromToolCall({ tool: "Read", file_path: "c:/users/dev/django/setup.py" }, ok, win);
    const b = fromToolCall({ tool: "Read", file_path: "C:\\USERS\\Dev\\django\\setup.py" }, ok, win);
    expect(a).toEqual(b);
  });

  it("keeps case on POSIX, where it is significant", () => {
    expect(fromToolCall({ tool: "Read", file_path: "/home/dev/django/README.rst" }, ok, posix)).toEqual({
      type: "read",
      path: "/home/dev/django/README.rst",
    });
  });

  it("Edit and Write are edits", () => {
    expect(fromToolCall({ tool: "Edit", file_path: "/home/dev/django/a.py" }, ok, posix)).toEqual({ type: "edit", path: "/home/dev/django/a.py" });
    expect(fromToolCall({ tool: "Write", file_path: "/home/dev/django/b.py" }, ok, posix)).toEqual({ type: "edit", path: "/home/dev/django/b.py" });
  });

  it("Grep and Glob hits come from the result, relative to the cwd", () => {
    const grep = fromToolCall(
      { tool: "Grep", pattern: "QuerySet" },
      { result: { mode: "files_with_matches", numFiles: 2, filenames: ["django\\db\\models\\query.py", ".\\tests\\basic\\tests.py"] } },
      win,
    );
    expect(grep).toEqual({
      type: "search",
      paths: ["c:/users/dev/django/django/db/models/query.py", "c:/users/dev/django/tests/basic/tests.py"],
    });
    const glob = fromToolCall({ tool: "Glob", pattern: "**/*.py" }, { result: { filenames: ["/home/dev/django/x.py", 3] } }, posix);
    expect(glob).toEqual({ type: "search", paths: ["/home/dev/django/x.py"] });
  });

  it("a files-with-matches search that found nothing is a search with no hits", () => {
    expect(fromToolCall({ tool: "Grep", pattern: "x" }, { result: { mode: "files_with_matches", filenames: [] } }, posix)).toEqual({
      type: "search",
      paths: [],
    });
    expect(fromToolCall({ tool: "Glob", pattern: "x" }, undefined, posix)).toEqual({ type: "search", paths: [] });
  });

  it("a subagent's read counts: it is Claude's attention too", () => {
    expect(fromToolCall({ tool: "Read", agentId: "sub-1", file_path: "/home/dev/django/a.py" }, ok, posix)).toEqual({
      type: "read",
      path: "/home/dev/django/a.py",
    });
  });

  it("MCP tools, other tools, Lens's own calls, failures and malformed calls are not", () => {
    expect(fromToolCall({ tool: "mcp__plugin_repowise_repowise__get_context", targets: ["a.py"] }, ok, posix)).toBeNull();
    expect(fromToolCall({ tool: "Bash", command: "cat a.py" }, ok, posix)).toBeNull();
    expect(fromToolCall({ tool: "Read", tool_use_id: "toolu_plugin_9", file_path: "/a.py" }, ok, posix)).toBeNull();
    expect(fromToolCall({ tool: "Read", file_path: "/a.py" }, { isError: true, result: "ENOENT" }, posix)).toBeNull();
    expect(fromToolCall({ tool: "Read" }, ok, posix)).toBeNull();
  });
});

describe("fromToolCall on recorded calls", () => {
  const of = (name: string) => fromToolCall(recorded[name]!.e, recorded[name]!.r, django);

  it("Grep files_with_matches: the hits, relative paths with backslashes resolved against the cwd", () => {
    expect(of("grep_files")).toEqual({
      type: "search",
      paths: [
        "c:/work/django/django/db/models/query.py",
        "c:/work/django/tests/queries/tests.py",
        "c:/work/django/tests/queries/test_qs_combinators.py",
        "c:/work/django/tests/queries/test_iterator.py",
      ],
    });
  });

  it("Grep content mode names no files: not a search, so earlier hits stay", () => {
    expect(of("grep_content")).toBeNull();
  });

  it("Glob: its filenames", () => {
    const glob = of("glob") as { type: string; paths: string[] };
    expect(glob.type).toBe("search");
    expect(glob.paths).toContain("c:/work/django/django/db/models/sql/query.py");
  });

  it("Read and a successful Edit: their absolute file_path", () => {
    expect(of("read")).toEqual({ type: "read", path: "c:/work/django/django/db/models/manager.py" });
    expect(of("edit")).toEqual({ type: "edit", path: "c:/work/django/django/db/models/manager.py" });
  });

  it("a failed Edit (isError, a string result) is nothing", () => {
    expect(of("edit_failed")).toBeNull();
  });
});

describe("paths", () => {
  it("absolutePath keeps case and joins relative paths to the cwd", () => {
    expect(absolutePath("django\\db\\Query.py", win)).toBe("C:/Users/Dev/django/django/db/Query.py");
    expect(absolutePath("./a.py", posix)).toBe("/home/dev/django/a.py");
    expect(absoluteKey("\\\\server\\share\\x.py", win)).toBe("//server/share/x.py");
  });

  it("relativeTo matches the root without case on Windows and keeps the file's own case", () => {
    expect(relativeTo("c:/users/dev/django/django/db/models/query.py", "C:\\Users\\Dev\\django\\", true)).toBe(
      "django/db/models/query.py",
    );
    expect(relativeTo("C:\\Users\\Dev\\django\\Docs\\Index.txt", "c:/users/dev/django", true)).toBe("Docs/Index.txt");
    expect(relativeTo("C:/Users/Dev/other/x.py", "C:/Users/Dev/django", true)).toBeNull();
    expect(relativeTo("/home/dev/Django/x.py", "/home/dev/django", false)).toBeNull();
    // A sibling whose name extends the root's is outside it.
    expect(relativeTo("/home/dev/django2/x.py", "/home/dev/django", false)).toBeNull();
  });

  it("a Windows read lands on its map cell regardless of separators and case", () => {
    const layout = layoutMap([{ file_path: "django/db/models/query.py", score: 6, nloc: 10 }], { columns: 4, rows: 2, caseInsensitive: true });
    const action = fromToolCall({ tool: "Read", file_path: "c:\\users\\dev\\DJANGO\\Django\\DB\\models\\query.py" }, ok, win);
    const trail = reduceTrail(initialTrail, action as TrailAction);
    expect(resolveLit(layout, litFromTrail(trail, "C:\\Users\\Dev\\django", true)).reads).toEqual([0]);
  });

  it("a POSIX read differing only in case is off the map", () => {
    const layout = layoutMap([{ file_path: "src/App.ts", score: 6, nloc: 10 }], { columns: 4, rows: 2, caseInsensitive: false });
    const trail = reduceTrail(initialTrail, { type: "read", path: "/repo/src/app.ts" });
    expect(notOnMap(layout, litFromTrail(trail, "/repo", false))).toBe(1);
  });
});

describe("trail reducer", () => {
  it("keeps reads in first-read order, once each", () => {
    let t = reduceTrail(initialTrail, { type: "read", path: "/a" });
    t = reduceTrail(t, { type: "read", path: "/b" });
    const same = reduceTrail(t, { type: "read", path: "/a" });
    expect(same).toBe(t);
    expect(t.reads).toEqual(["/a", "/b"]);
  });

  it("keeps only the newest reads past the cap", () => {
    let t = initialTrail;
    for (let i = 0; i < 205; i++) t = reduceTrail(t, { type: "read", path: `/f${i}` });
    expect(t.reads).toHaveLength(200);
    expect(t.reads[0]).toBe("/f5");
    expect(t.readsCapped).toBe(true);
  });

  it("each search replaces the hits and counts", () => {
    let t = reduceTrail(initialTrail, { type: "search", paths: ["/a"] });
    t = reduceTrail(t, { type: "search", paths: ["/a"] });
    expect(t.hits).toEqual(["/a"]);
    expect(t.searches).toBe(2);
  });

  it("an edit is also a read, and starts loading its callers", () => {
    const t = reduceTrail(initialTrail, { type: "edit", path: "/q.py" });
    expect(t).toMatchObject({ reads: ["/q.py"], edit: "/q.py", edits: 1, callers: { status: "loading" } });
  });

  it("drops callers that answer an older edit", () => {
    let t = reduceTrail(initialTrail, { type: "edit", path: "/a.py" });
    t = reduceTrail(t, { type: "edit", path: "/b.py" });
    const stale = reduceTrail(t, { type: "callers", edits: 1, callers: { status: "ready", paths: ["x"] } });
    expect(stale).toBe(t);
    const fresh = reduceTrail(t, { type: "callers", edits: 2, callers: { status: "failed" } });
    expect(fresh.callers).toEqual({ status: "failed" });
  });
});
