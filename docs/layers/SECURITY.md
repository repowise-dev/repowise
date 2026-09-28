# Security Signals

Repowise records a small set of security signals while it indexes: pattern
matches over source text, and symbol names that look security-relevant. Pure
regex and SQL, no LLM calls, no network, no dependency resolution.

This is a floor, not a scanner. The registry holds twenty-two patterns. It has no
model of your framework, no notion of which inputs are attacker-controlled, and
no dataflow: it cannot tell a parameterised query from a concatenated one beyond
what the surrounding characters give away, and it cannot tell whether a
dangerous call is reachable. Everything below is written so you can see exactly
where that floor sits, because a security surface that overstates itself is
worse than one that reports nothing.

Run a real SAST tool alongside it. Nothing here replaces one.

## Quick start

```bash
repowise init                       # working-tree signals populate during indexing
repowise security scan --history    # walk every tracked revision for leaked secrets
repowise security scan --history --since v1.0.0 --to HEAD
repowise security scan --history --all-patterns --format json
repowise security check origin/main...HEAD   # CI gate on what a change adds (see below)
```

`scan --history` stores what it finds and prints counts, not a list:

```
repowise security scan --history
  Commits scanned: 412
  Blobs scanned:   2318
  Files scanned:   1604
  Findings stored: 8
  By severity:     high=6, low=2
  By kind:         aws_access_key=1, hardcoded_password=2, hardcoded_secret=5
```

The findings themselves are read where the stored rows surface, below.

From an agent, through the risk surface:

```python
get_risk(target="src/api/")     # includes a security_signals block for the target
```

Findings also appear on the Security tab of the code-health page, and at
`GET /api/repos/{repo_id}/security`.

## What the registry catches

Twenty-two patterns plus a symbol-name scan, giving twenty-three kinds across
three severities. Severity is a fixed property of the pattern, with one
exception: a secret kind found in test material, or under a `test`, `spec`,
`fixtures`, `mock` or `example` directory (and their plural and dunder forms),
is recorded at `low`, since those are mostly fake keys but a real one is still
worth seeing. Nothing is scored, ranked, or aggregated.

| Kind | Severity | Matches |
|------|----------|---------|
| `eval_call` | high | `eval(...)`, including a receiver chain (`vm.eval(`, `foo.bar.eval(`) |
| `exec_call` | high | `exec(...)`, same receiver handling. Outside Python only in a file that names `child_process`, and there also `execFile` / `execSync` |
| `pickle_loads` | high | `pickle.loads` |
| `subprocess_shell_true` | high | `subprocess.*` with `shell=True`, including across physical lines |
| `os_system` | high | `os.system` |
| `hardcoded_password` | high | an assignment of a quoted literal to a name containing `password`, any case |
| `hardcoded_secret` | high | the same for a name containing `api_key`, `apikey`, `secret`, `token` or `access_key`. A snake_case value (a constant holding a key's name) or a template placeholder (`{{ ... }}`, `${...}`) is skipped, and a value one of the vendor kinds below already reports is not reported twice |
| `aws_access_key` | high | an AWS access key ID (`AKIA`/`ASIA` + 16 chars), regardless of variable name |
| `github_token` | high | a GitHub PAT, OAuth, app, or refresh token (`ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`/`github_pat_`), regardless of variable name |
| `slack_token` | high | a Slack token (`xoxb-`, `xoxa-`, `xoxp-`, `xoxr-`, `xoxs-`), regardless of variable name |
| `google_api_key` | high | a Google API key (`AIza` + 35 chars), regardless of variable name, but not from the middle of a base64 run such as a lockfile integrity hash |
| `stripe_key` | high | a Stripe secret or restricted key (`sk_` or `rk_`, then `live_`, `test_` or `prod_`), regardless of variable name. Publishable `pk_` keys are public by design and do not fire |
| `private_key_pem` | high | a PEM header (`-----BEGIN ... PRIVATE KEY-----`) followed by a base64-looking body line, so assembling just the header string does not fire. The break may be an escaped `\n`, which catches keys held on one line in JSON or `.env` files |
| `fstring_sql` | med | an f-string containing `SELECT` and an interpolation |
| `concat_sql` | med | `.execute("SELECT ... +` |
| `tls_verify_false` | med | `verify = False` |
| `weak_hash` | low | the words `md5` or `sha1` |
| `unsafe_inner_html` | med | `__html:` (React's `dangerouslySetInnerHTML` shape) assigned a non-literal value |
| `template_literal_sql` | med | a JS/TS template literal containing `SELECT`+`FROM` or `UPDATE`+`SET` and an interpolation |
| `public_env_secret` | high | a secret-shaped name (`API_KEY`, `SECRET`, `TOKEN`, `PASSWORD`) behind a `NEXT_PUBLIC_` or `VITE_` prefix, excluding `..._ANON_...` |
| `new_function_call` | high | `new Function(...)` |
| `reject_unauthorized_false` | med | `rejectUnauthorized: false` |
| `security_sensitive_symbol` | low | a symbol whose name contains `auth`, `token`, `password`, `jwt`, `session` or `crypto` |

The last one is informational. It flags nothing wrong; it marks where the
security-relevant code lives.

Two details worth knowing:

**`eval` and `exec` are resolved properly, the rest are not.** In Python files
they are found by walking the AST, so a match is a real call rather than a
substring, with a bounded lexical fallback when the file does not parse. Other
languages get the lexical path, over source with comments and string literals
masked out, so an `eval(` inside a comment does not fire. Outside Python
`exec_call` carries one more condition: the file has to name `child_process`,
searched in raw source because the module usually arrives as a string literal
that masking would blank. Every other pattern in the table is a plain regex over
one line of raw source, comments included.

**Two patterns see across lines.** `private_key_pem` needs the body line after
its header, and `subprocess.run(` opening on one line with
`shell=True` several lines down is invisible to a per-line scan, so
`subprocess_shell_true` gets a second pass over the whole source. Continuation
is restricted to lines that begin with indentation and capped at roughly 200
characters, so a closed call cannot reach forward into an unrelated one.

## What the registry does not catch

Stated plainly, because the table above reads like more coverage than it is.

**Most languages.** The registry is shaped by Python, with five patterns added
for JavaScript and TypeScript (below). `pickle.loads`, `os.system`,
`subprocess(shell=True)` and `verify=False` are still Python-only idioms;
`fstring_sql` is a Python f-string. On a Go, Rust or Java codebase, `eval` /
`exec` and the two secret patterns are most of what can fire. A repo in one of
those languages reporting few findings is reporting the registry's shape, not
its own health.

**Anything needing framework semantics.** Missing authorization on a route
handler, permissive CORS on a mutating endpoint, an unvalidated redirect, an
object reference with no ownership check. These require knowing what a route
handler is, which ones mutate, and what counts as a gate. A regex cannot do it
and this layer does not try.

**Anything needing dataflow.** Whether attacker-controlled input actually
reaches a dangerous call is not computed. `eval_call` fires the same on a
constant and on a request parameter.

**Real secret detection.** `hardcoded_password` / `hardcoded_secret` match a
literal assignment to a variable named like a credential, in any case, and the
six value-shape kinds above (AWS, GitHub, Slack, Google, Stripe, PEM) catch a
handful of common vendor formats regardless of variable name. That is still far
short of a real scanner: no entropy scoring, and any format or provider not in
that short list is invisible. For secret scanning proper, run gitleaks or
trufflehog; history mode below is complementary to those, not a replacement.

**Dependencies.** No CVE lookup, no advisory feed, no SBOM. Nothing here looks
outside your source.

**False positives are expected.** `weak_hash` fires on the word `md5` anywhere,
including in a comment explaining why md5 was removed. `hardcoded_password`
fires on test fixtures and on empty placeholder credentials. The layer reports
signals for a human to read, and it is tuned to say too much rather than too
little.

**Two of them are worth knowing before you read a report.**

`exec` is not a global in JavaScript; the name belongs to `RegExp.prototype.exec`,
so the receiver-chain prefix in the pattern would otherwise match
`re.exec(expr)`, `/x/.exec(s)` and `cellPattern.exec(xml)` — ordinary parsing
code — at `high`. Outside Python the kind is gated on the file naming
`child_process`, which is where the dangerous call comes from. The gate is per
file rather than per call, so a file that both spawns a process and parses text
with regexes still reports every `exec(` in it. That residual is deliberate,
and covered by a test rather than left implicit.

The per-line patterns run on raw source, comments included. A comment that
spells out a credential assignment reports itself as a `hardcoded_secret`, and
`weak_hash` fires on a comment explaining why md5 was removed. Only the
`eval`/`exec` path masks comments and string literals; nothing else does.

**Five patterns for JavaScript and TypeScript, measured the same way as the
`exec_call` and secret-case fixes above** — a 17-repository, 1109-file corpus,
every hit adjudicated by hand. Three of the five needed tightening before they
were worth shipping:

| Kind | First cut | Verified real | After tightening | The noise |
|---|---|---|---|---|
| `unsafe_inner_html` | 15 | 0 | 4 | every hit a source-pinned stylesheet constant referenced by name |
| `template_literal_sql` | 10 | 2 | 2 | prose (`` `Order update failed: ${status}` ``) and a Tailwind class (`select-none`) |
| `public_env_secret` | 9 | 3 | 3 | Supabase anon keys — public by design, protected by row-level security |
| `new_function_call` | 0 | 0 | 0 | — |
| `reject_unauthorized_false` | 0 | 0 | 0 | — |

`unsafe_inner_html` ships at `med`, not `high`, because of that first row: a
name reference cannot be told apart from a pinned constant without dataflow,
so the pattern is a places-to-read signal, not a confirmed sink. The same
gap explains why the value test (`__html:` followed by an identifier, `$`, or
`(`) is written as a positive lookahead rather than a negative one excluding
quotes — tried at the position *before* the preceding `\s*`, a negative
lookahead would trivially pass and let a string literal through anyway.

`public_env_secret` excludes any name containing `ANON`, which is the one
carve-out narrow enough to state as a rule rather than a heuristic: an
`..._ANON_...` key is meant to be public. Nothing else in the corpus noise was
worth a similarly specific exclusion.

`template_literal_sql` requires the SQL verb's companion clause —
`SELECT`+`FROM`, `UPDATE`+`SET` — because the bare verb is an ordinary English
word and fires on both prose and a Tailwind utility class otherwise.

## Working tree versus history

Two scan surfaces share the registry and the storage.

**Working tree** runs during `repowise init` and `repowise update`, over the
files as they exist now. Rows are stored with an empty `commit_sha`. There is no
separate command for it: `repowise security scan` without `--history` is a stub
that prints a hint, because re-scanning the working tree outside indexing would
duplicate what indexing just did.

**History** runs only on `repowise security scan --history`, walking every
tracked revision of every source file. Rows carry the SHA and author date of the
commit that introduced the match. This finds what the working tree cannot: a
credential committed in March and removed in April is absent from HEAD and
present in the clone forever.

History mode reports **only the `SECRET_KINDS` (genuine leaked-credential)
kinds** by default — `hardcoded_password`, `hardcoded_secret`, and the six
vendor value-shape kinds above. The reasoning is asymmetry of decay. A commit
that once called `eval()`
is history doing what history does — the code changed, that is the point, and
reporting every such moment across every revision buries the surface in noise. A
committed secret does not decay. It stays valid until someone rotates it, and
whoever cloned the repo still has it. Pass `--all-patterns` to get the code-smell
kinds across history too, and expect volume.

Both paths land in the same `security_findings` table, with a unique constraint
on `(repository_id, file_path, kind, line_number, commit_sha)`. Re-running either
scan is idempotent.

## In CI: `repowise security check`

A third surface, for pull requests. It needs git and nothing else: no index,
no database, no API key, and it stores nothing.

```bash
repowise security check origin/main...HEAD
repowise security check --fail-on med --format github
repowise security check --format sarif > security.sarif
```

It judges what the change adds, not the repository:

- **At the change's head**, every file the change touched is scanned as the
  head commit has it, and a finding counts only when a line it spans is one
  the change added or edited. The two multi-line kinds count when any line of
  their span changed, so editing only the `shell=True` line of a call, or only
  the body of a PEM key, still counts. In a documentation file (`.md`, `.mdx`,
  `.rst`, `.txt`, `.adoc`) only the secret kinds count: prose naming
  `pickle.loads` is not a call, but a key pasted into a README is a leak.
- **Inside the change**, every commit in the range is scanned the same way
  for the secret kinds only. A key committed in one commit and deleted in the
  next is absent from the head and present in every clone; the gate reports it
  with the commit that added it and says to rotate it. Merge commits are
  skipped, so merging the target branch in does not blame its lines on the
  change. A code smell added and removed inside the change does not count.

`--fail-on` names the lowest severity that fails (`high` by default, then
`med`, `low`). Exit codes are the ones every repowise CI gate uses: `0` passed,
`1` failed, `2` could not evaluate (not a git repository, unknown revision, no
merge-base in a shallow clone, a commit of the change cut off by a shallow
clone, an unreadable baseline). A git error is never read as a clean change.

Every format carries the masked snippet only, because CI logs are often
public: `table`, `json`, `markdown`, `github` (annotations plus the job
summary) and `sarif` (for code-scanning upload). The gate is the registry
above and has its limits: a pass means no pattern matched a changed line.

**Baseline.** `--write-baseline FILE` records the change's findings in a
committed JSON file and exits 0; `--baseline FILE` accepts what it lists, so
only new findings fail. A finding is keyed on its file, kind and masked
matched line, never on the raw value, so it stays accepted when an edit above
it shifts its line number and changes when the matched line itself changes.
Because the gate sees one change at a time, writing to an existing baseline
adds to its entries instead of replacing them; remove an entry by deleting it
from the file.

GitHub Actions, with the SARIF uploaded from your own workflow:

```yaml
on: pull_request
permissions:
  contents: read
  security-events: write
jobs:
  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # the gate reads every commit of the change
      - run: pip install repowise
      - run: repowise security check --format github --baseline .security-baseline.json
      - if: always()
        run: repowise security check --format sarif --baseline .security-baseline.json > security.sarif || true
      - if: always()
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: security.sarif
```

Without a REVSPEC the base comes from the CI's pull-request variables
(`GITHUB_BASE_REF`, GitLab's `CI_MERGE_REQUEST_TARGET_BRANCH_NAME`, Jenkins'
`CHANGE_TARGET`, Bitbucket's `BITBUCKET_PR_DESTINATION_BRANCH`), else the
remote's default branch. A shallow checkout cannot be read commit by commit,
so fetch the full history; the gate exits 2 rather than guess.

## Line verification

A finding's `line_number` is written at scan time, and the file moves on. A wrong
line on a security finding is worse than none: it sends the reader to innocent
code while looking authoritative. So the line is re-checked against the live file
every time a finding is served, using the stored snippet: the matched line,
stripped and trimmed to 120 characters, with every credential value on it
masked to its first four characters and `****`. An unmasked snippet is a
substring of the line it came from; a masked one is matched on the text before
the first `****`.

Three outcomes, on every finding the API returns:

| `line_verified` | `line_number` | Meaning |
|---|---|---|
| `true` | a line | The snippet is on that line. Either it never moved, or it moved and the line was corrected. |
| `false` | a line | The snippet occurs more than once in the file. The line is a guess and the surface should mark it as one. |
| `false` | `null` | The snippet is gone from the file. The finding is stale; showing a line would point at unrelated code. |

`security_sensitive_symbol` is the exception. Its snippet is a bare identifier
rather than a line of code, and an identifier recurs throughout a file, so
relocating on it would land somewhere arbitrary. Those findings are checked in
place and never relocated or withdrawn: if the name is not on the stored line
they are returned unverified rather than treated as gone.

When the file cannot be read at all — the repo is not checked out where the
server expects it — the stored line is passed through unverified rather than
claimed as correct.

## Storage

One table, `security_findings`, written by both scan paths:

| Column | Notes |
|---|---|
| `file_path` | Repo-relative |
| `kind` | One of the kinds in the table above |
| `severity` | `high`, `med`, `low`: fixed per pattern, except a secret kind in test material is `low` |
| `snippet` | Matched line, trimmed to 120 characters, credential values masked (`AKIA****`); a symbol name for `security_sensitive_symbol`. The raw value is never stored |
| `line_number` | As of scan time; verified at serve time |
| `commit_sha` | Empty for working-tree rows, the introducing commit for history rows |
| `commit_at` | Author date, history rows only |

Indexing against a database that has not yet migrated the table skips
persistence silently; every other write failure is a real error.

## Reading the surface honestly

A useful way to read a repo's findings, in order:

1. **Any `SECRET_KINDS` finding from history mode** (`hardcoded_secret`,
   `hardcoded_password`, or one of the vendor value-shape kinds). These
   are the findings most likely to be both true and actionable. Rotate first,
   remove from history second.
2. **The high-severity working-tree kinds**, as a list of places to read rather
   than a list of bugs. The layer found a call; you decide whether it is
   reachable.
3. **`security_sensitive_symbol` as a map.** Where the auth and crypto code
   lives is useful context for review, and for `get_risk` when you are about to
   change one of those files.
4. **Low findings last, and sceptically.** `weak_hash` in particular is a word
   match.

And once more, because it is the whole point of this page: a low finding count
means the registry did not match. On most repos, in most languages, that is what
it means and nothing more.
