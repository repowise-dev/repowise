import { describe, expect, it } from "vitest";
import { fileTarget } from "../src/model/events";
import { initialSession, reduce, type SessionState } from "../src/model/session";
import { spinnerLine } from "../src/views/copy";
import { spinnerSuffix } from "../src/views/spinner";

describe("fileTarget", () => {
  const win = "C:\\work\\requests";

  it("is the repo-relative path of a Read, Edit or Write inside the repo", () => {
    for (const tool of ["Read", "Edit", "Write"]) {
      expect(fileTarget({ tool, file_path: "C:\\work\\requests\\src\\requests\\sessions.py" }, win)).toBe(
        "src/requests/sessions.py",
      );
    }
  });

  it("compares Windows paths without case and either separator", () => {
    expect(fileTarget({ tool: "Read", file_path: "c:/Work/Requests/src/api.py" }, win)).toBe("src/api.py");
    expect(fileTarget({ tool: "Read", file_path: "C:\\work\\requests\\a.py" }, "C:\\work\\requests\\")).toBe("a.py");
  });

  it("keeps case on POSIX paths", () => {
    expect(fileTarget({ tool: "Read", file_path: "/work/app/src/x.ts" }, "/work/app")).toBe("src/x.ts");
    expect(fileTarget({ tool: "Read", file_path: "/Work/app/src/x.ts" }, "/work/app")).toBeNull();
  });

  it("is null for other tools, files outside the repo, a sibling with the same prefix, or no repo", () => {
    expect(fileTarget({ tool: "Bash", file_path: "C:\\work\\requests\\a.py" }, win)).toBeNull();
    expect(fileTarget({ tool: "Grep" }, win)).toBeNull();
    expect(fileTarget({ tool: "Read", file_path: "C:\\work\\requests-old\\a.py" }, win)).toBeNull();
    expect(fileTarget({ tool: "Read", file_path: "C:\\elsewhere\\a.py" }, win)).toBeNull();
    expect(fileTarget({ tool: "Read", file_path: 42 }, win)).toBeNull();
    expect(fileTarget({ tool: "Read", file_path: "C:\\work\\requests\\a.py" }, null)).toBeNull();
  });
});

describe("running tool and context reducers", () => {
  const ctx = { callerFiles: 41, contributors: 3, hotspot: null, recentOwner: null };

  it("tracks the file tool running now; an overlapping call's end does not clear it", () => {
    let s = reduce(initialSession, { type: "toolStarted", tool: { id: "a", file: "x.py" } });
    s = reduce(s, { type: "toolStarted", tool: { id: "b", file: "y.py" } });
    expect(reduce(s, { type: "toolEnded", id: "a" })).toBe(s);
    expect(reduce(s, { type: "toolEnded", id: "b" }).running).toBeNull();
  });

  it("keeps each file's context", () => {
    const s = reduce(initialSession, { type: "contextLoaded", file: "x.py", context: ctx });
    expect(reduce(s, { type: "contextLoaded", file: "y.py", context: ctx }).contexts).toEqual({ "x.py": ctx, "y.py": ctx });
  });

  it("remembers the repo root only while indexed", () => {
    const s = reduce(initialSession, { type: "discovered", mode: "lite", freshness: null, repoRoot: "C:\\r" });
    expect(s.repoRoot).toBe("C:\\r");
    expect(reduce(s, { type: "discovered", mode: "no-cli", freshness: null, repoRoot: "C:\\r" }).repoRoot).toBeNull();
    expect(reduce(initialSession, { type: "discovered", mode: "full", freshness: null }).repoRoot).toBeNull();
  });
});

describe("spinner suffix", () => {
  const running: SessionState = { ...initialSession, running: { id: "a", file: "django/db/models/query.py" } };

  it("names the file, its caller files and contributors once the context has landed", () => {
    const s = { ...running, contexts: { "django/db/models/query.py": { callerFiles: 41, contributors: 3, hotspot: null, recentOwner: null } } };
    expect(spinnerSuffix(s)).toBe("query.py · 41 caller files · 3 contributors");
  });

  it("says nothing while the context is still on its way, or when no file tool runs", () => {
    expect(spinnerSuffix(running)).toBeNull();
    expect(spinnerSuffix({ ...running, running: null, contexts: { "a.py": { callerFiles: 1, contributors: 1, hotspot: null, recentOwner: null } } })).toBeNull();
  });

  it("uses singulars, thousands separators, and leaves out unknown or zero counts", () => {
    expect(spinnerLine("a.py", { callerFiles: 1, contributors: 1, hotspot: null, recentOwner: null })).toBe("a.py · 1 caller file · 1 contributor");
    expect(spinnerLine("src/a.py", { callerFiles: 1204, contributors: null, hotspot: null, recentOwner: null })).toBe("a.py · 1,204 caller files");
    expect(spinnerLine("a.py", { callerFiles: 0, contributors: null, hotspot: null, recentOwner: null })).toBe("a.py");
  });
});
