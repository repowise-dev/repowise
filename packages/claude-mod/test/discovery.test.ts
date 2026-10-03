import { afterEach, describe, expect, it } from "vitest";
import { configureApiClient } from "@repowise-dev/api-client";
import {
  discover,
  findIndexedRoot,
  isPidAlive,
  isLoopbackUrl,
  isWindowsPath,
  mcpReachable,
  probeServer,
  readFreshness,
  readServeLock,
} from "../src/data/discovery";
import { TimeoutError, withTimeout } from "../src/data/transport";
import { failed, fakeHost, fixture, json, ok, type FakeHostOptions } from "./fake-host";

afterEach(() => configureApiClient({ baseUrl: "" }));

const ROOT = "C:\\work\\requests";
const STATE = `${ROOT}/.repowise/state.json`;
const LOCK = `${ROOT}/.repowise/serve.lock.json`;
const repos = JSON.parse(fixture("repos.json")) as unknown;
const indexedAt = "a".repeat(40);
const head = "b".repeat(40);

/** tasklist answers for pid 4242 only. */
function tasklist(argv: readonly string[]) {
  if (argv[0] === "tasklist") return argv[2] === "PID eq 4242" ? ok('"python.exe","4242","Console"\r\n') : ok("INFO: No tasks are running.\r\n");
  if (argv[0] === "git") return failed;
  throw new Error(`unexpected ${argv.join(" ")}`);
}

function server(status = 200) {
  return (url: string) => {
    if (url.endsWith("/health")) return json(200, { status: "ok", db: "ok", version: "0.54.0" });
    if (url.endsWith("/api/repos")) return status === 200 ? json(200, repos) : json(status, { detail: "Invalid API key" });
    return json(404, { detail: "not found" });
  };
}

const inWorkTree = (argv: readonly string[]) =>
  argv.join(" ") === "git rev-parse --is-inside-work-tree" ? ok("true\n") : failed;

describe("isLoopbackUrl", () => {
  it.each(["http://127.0.0.1:7411", "http://localhost:7411/", "http://[::1]:7411"])("accepts %s", (url) => {
    expect(isLoopbackUrl(url)).toBe(true);
  });

  it.each([
    "https://127.0.0.1:7411",
    "http://evil.example:7411",
    "http://127.0.0.1.evil.example",
    "http://10.0.0.5:7411",
    "file:///etc/passwd",
    "not a url",
  ])("refuses %s", (url) => {
    expect(isLoopbackUrl(url)).toBe(false);
  });
});

function host(o: FakeHostOptions = {}) {
  return fakeHost({ cwd: ROOT, files: { [STATE]: JSON.stringify({ last_sync_commit: indexedAt }) }, run: tasklist, ...o });
}

describe("lock file", () => {
  it("parses a valid lock", async () => {
    const lock = await readServeLock(host({ files: { [LOCK]: fixture("locks/valid.json") } }), ROOT);
    expect(lock).toMatchObject({ pid: 4242, url: "http://127.0.0.1:7411" });
  });

  it("returns null for a garbage lock", async () => {
    expect(await readServeLock(host({ files: { [LOCK]: fixture("locks/garbage.json") } }), ROOT)).toBeNull();
  });

  it("returns null for a missing lock", async () => {
    expect(await readServeLock(host({ files: {} }), ROOT)).toBeNull();
  });

  it("still parses a stale lock; the pid gate decides", async () => {
    const h = host({ files: { [LOCK]: fixture("locks/stale-pid.json") } });
    const lock = await readServeLock(h, ROOT);
    expect(lock?.pid).toBe(9999);
    expect(await isPidAlive(h, 9999, ROOT)).toBe(false);
    expect(await isPidAlive(h, 4242, ROOT)).toBe(true);
  });
});

describe("pid probe", () => {
  it("uses ps off Windows", async () => {
    const h = fakeHost({ run: (argv) => (argv[2] === "77" ? ok("   77\n") : failed) });
    expect(await isPidAlive(h, 77, "/home/u/repo")).toBe(true);
    expect(await isPidAlive(h, 78, "/home/u/repo")).toBe(false);
    expect(h.calls.run[0]).toEqual(["ps", "-p", "77", "-o", "pid="]);
  });

  it("is unknown, not dead, when the process API is missing", async () => {
    expect(await isPidAlive(fakeHost(), 1, ROOT)).toBeNull();
  });
});

describe("findIndexedRoot", () => {
  it("walks up from a subdirectory to the indexed root", async () => {
    expect(await findIndexedRoot(host(), `${ROOT}\\src\\requests`)).toBe(ROOT);
  });

  it("does not take a .repowise dir without a state file (the user config dir)", async () => {
    const h = fakeHost({ files: { "C:\\Users\\me/.repowise/config.yaml": "" } });
    expect(await findIndexedRoot(h, "C:\\Users\\me\\scratch")).toBeNull();
  });

  it("works on POSIX paths up to the root", async () => {
    const h = fakeHost({ files: { "/.repowise/state.json": "{}" } });
    expect(await findIndexedRoot(h, "/srv/app")).toBe("/");
    expect(await findIndexedRoot(fakeHost(), "/srv/app")).toBeNull();
  });

  it("detects Windows paths", () => {
    expect(isWindowsPath("C:\\x")).toBe(true);
    expect(isWindowsPath("c:/x")).toBe(true);
    expect(isWindowsPath("\\\\server\\share")).toBe(true);
    expect(isWindowsPath("/home/x")).toBe(false);
  });
});

describe("probeServer", () => {
  const lock = JSON.parse(fixture("locks/valid.json"));

  it("matches this repo by local_path among several, not the first row", async () => {
    expect(await probeServer(host({ http: server() }), lock, ROOT)).toEqual({ kind: "ok", repoId: "repo-requests" });
  });

  it("reports a server that does not list this repo", async () => {
    expect(await probeServer(host({ http: server() }), lock, "C:\\elsewhere")).toEqual({ kind: "unlisted" });
  });

  it("reads a 401 as a configured key, never sending one", async () => {
    const seen: Record<string, string>[] = [];
    const h = host({
      http: (url, init) => {
        seen.push(init.headers ?? {});
        return server(401)(url);
      },
    });
    expect(await probeServer(h, lock, ROOT)).toEqual({ kind: "auth" });
    for (const headers of seen) expect(Object.keys(headers).map((k) => k.toLowerCase())).not.toContain("authorization");
  });

  it("is down when /health does not answer", async () => {
    expect(await probeServer(host(), lock, ROOT)).toEqual({ kind: "down" });
  });

  it("is down when the repo list fails for another reason", async () => {
    expect(await probeServer(host({ http: server(500) }), lock, ROOT)).toEqual({ kind: "down" });
  });
});

describe("discover", () => {
  it("full: live lock, healthy server, repo listed", async () => {
    const h = host({ files: { [STATE]: "{}", [LOCK]: fixture("locks/valid.json") }, http: server() });
    expect(await discover(h)).toEqual({ mode: "full", repoRoot: ROOT, repoId: "repo-requests" });
    expect(h.calls.connect).toBe(0);
  });

  it("lite: a stale pid is never probed", async () => {
    const h = host({ files: { [STATE]: "{}", [LOCK]: fixture("locks/stale-pid.json") }, http: server() });
    expect(await discover(h)).toEqual({ mode: "lite", liteReason: "no-server", repoRoot: ROOT });
    expect(h.calls.http).toEqual([]);
  });

  it("lite: garbage or missing lock", async () => {
    for (const files of [{ [STATE]: "{}", [LOCK]: fixture("locks/garbage.json") }, { [STATE]: "{}" }]) {
      const h = host({ files });
      expect((await discover(h)).mode).toBe("lite");
    }
  });

  it("lite with a reason when the server wants a key", async () => {
    const h = host({ files: { [STATE]: "{}", [LOCK]: fixture("locks/valid.json") }, http: server(401) });
    expect(await discover(h)).toMatchObject({ mode: "lite", liteReason: "auth" });
  });

  it("probes /health when the pid cannot be checked", async () => {
    const h = host({ files: { [STATE]: "{}", [LOCK]: fixture("locks/valid.json") }, run: undefined, http: server() });
    expect((await discover(h)).mode).toBe("full");
  });

  it("no-index: a git work tree with no state file anywhere up the tree", async () => {
    const h = fakeHost({ cwd: ROOT, run: inWorkTree });
    expect(await discover(h)).toEqual({ mode: "no-index", repoRoot: null });
    expect(h.calls.run).toContainEqual(["git", "rev-parse", "--is-inside-work-tree"]);
  });

  it("no-repo: outside a git work tree Lens stays quiet and asks nothing of MCP", async () => {
    for (const run of [() => failed, undefined]) {
      const h = fakeHost({ cwd: "C:\Users\me", run });
      expect(await discover(h)).toEqual({ mode: "no-repo", repoRoot: null });
      expect(h.calls.connect).toBe(0);
    }
  });

  it("no-cli: the MCP server cannot connect", async () => {
    expect((await discover(host({ files: { [STATE]: "{}" }, connected: false }))).mode).toBe("no-cli");
    expect((await discover(fakeHost({ cwd: ROOT, run: inWorkTree, connected: false }))).mode).toBe("no-cli");
  });

  it("never probes a lock that names a host other than this machine", async () => {
    const remote = { ...JSON.parse(fixture("locks/valid.json")), url: "http://evil.example:7411", pid: 4242 };
    const h = host({ files: { [STATE]: "{}", [LOCK]: JSON.stringify(remote) }, http: server() });
    expect(await discover(h)).toEqual({ mode: "lite", liteReason: "no-server", repoRoot: ROOT });
    expect(h.calls.http).toEqual([]);
  });

  it("no-cli when the connect itself rejects", async () => {
    const h = fakeHost({ cwd: ROOT });
    h.mcp.connect = async () => {
      throw new Error("refused");
    };
    expect(await mcpReachable(h)).toBe(false);
  });
});

describe("readFreshness", () => {
  function git(diff: string | null, headSha = head) {
    return (argv: readonly string[]) => {
      if (argv[0] !== "git") return tasklist(argv);
      if (argv[1] === "rev-parse") return ok(`${headSha}\n`);
      if (argv[1] === "diff") return diff === null ? failed : ok(diff);
      return failed;
    };
  }

  it("counts files behind HEAD with read-only git", async () => {
    const h = host({ run: git("a.py\nb.py\n\nc.py\n") });
    expect(await readFreshness(h, ROOT)).toEqual({ changedFiles: 3 });
    expect(h.calls.run).toContainEqual(["git", "diff", "--name-only", indexedAt, head, "--"]);
    for (const argv of h.calls.run.filter((a) => a[0] === "git")) expect(["rev-parse", "diff"]).toContain(argv[1]);
  });

  it("never hands git a state value that is not a bare sha", async () => {
    for (const hostile of ["--output=C:/pwned.txt", "-p", "HEAD~1", "a".repeat(65), "abc"]) {
      const h = host({ files: { [STATE]: JSON.stringify({ last_sync_commit: hostile }) }, run: git("a.py\n") });
      expect(await readFreshness(h, ROOT)).toBeNull();
      expect(h.calls.run.filter((a) => a[0] === "git")).toEqual([]);
    }
  });

  it("ignores a HEAD that is not a sha", async () => {
    expect(await readFreshness(host({ run: git("a.py\n", "--output=x") }), ROOT)).toBeNull();
  });

  it("says nothing when current", async () => {
    expect(await readFreshness(host({ run: git("", indexedAt) }), ROOT)).toBeNull();
  });

  it("keeps the row but drops the number when the diff fails", async () => {
    expect(await readFreshness(host({ run: git(null) }), ROOT)).toEqual({ changedFiles: null });
  });

  it("says nothing without git or without a synced commit", async () => {
    expect(await readFreshness(host({ run: () => failed }), ROOT)).toBeNull();
    expect(await readFreshness(host({ files: { [STATE]: "{}" }, run: git("x") }), ROOT)).toBeNull();
  });

  it("stays quiet while an update holds its lock with a live pid", async () => {
    const files = {
      [STATE]: JSON.stringify({ last_sync_commit: indexedAt }),
      [`${ROOT}/.repowise/.update.lock`]: JSON.stringify({ pid: 4242, started_at: 1 }),
    };
    expect(await readFreshness(host({ files, run: git("a.py\n") }), ROOT)).toBeNull();
  });

  it("ignores an update lock whose pid is gone", async () => {
    const files = {
      [STATE]: JSON.stringify({ last_sync_commit: indexedAt }),
      [`${ROOT}/.repowise/.update.lock`]: JSON.stringify({ pid: 9999, started_at: 1 }),
    };
    expect(await readFreshness(host({ files, run: git("a.py\n") }), ROOT)).toEqual({ changedFiles: 1 });
  });
});

describe("withTimeout", () => {
  it("rejects with a TimeoutError when the work does not settle", async () => {
    await expect(withTimeout(new Promise(() => {}), 5, "probe")).rejects.toBeInstanceOf(TimeoutError);
    await expect(withTimeout(Promise.resolve(1), 50, "probe")).resolves.toBe(1);
  });
});
