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
exception: any finding in test material, or under a directory named
`test`, `tests`, `__tests__`, `__test__`, `fixtures`, `__fixtures__`, `spec`,
`specs`, `mock`, `mocks`, `__mocks__`, `example` or `examples`, is recorded at
`low`. Keys there are mostly fake and calls there do not ship, but a real one
is still worth seeing. Nothing is scored, ranked, or aggregated.

| Kind | Severity | Matches |
|------|----------|---------|
| `eval_call` | high | `eval(...)`, including a receiver chain (`vm.eval(`, `foo.bar.eval(`) |
| `exec_call` | high | `exec(...)`, same receiver handling. Outside Python only a call through a name bound to `child_process` (a namespace, a named import, or the `require` result), covering `execFile` / `execSync` too |
| `pickle_loads` | high | `pickle.loads` |
| `subprocess_shell_true` | high | `subprocess.*` with `shell=True`, including across physical lines |
| `os_system` | high | `os.system` |
| `hardcoded_password` | high | an assignment of a quoted literal to a name containing `password`, any case, where the name is code (not inside a comment, docstring or string) |
| `hardcoded_secret` | high | the same for a name containing `api_key`, `apikey`, `secret`, `token` or `access_key`. A snake_case value (a constant holding a key's name) or a template placeholder (`{{ ... }}`, `${...}`, `$(...)`) is skipped, and a value one of the vendor kinds below already reports is not reported twice |
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
| `public_env_secret` | high | a secret-shaped name (`API_KEY`, `SECRET`, `TOKEN`, `PASSWORD`) behind a `NEXT_PUBLIC_` or `VITE_` prefix, excluding `..._ANON_...`, outside comments |
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
`exec_call` carries one more condition: the call has to go through a name the
file binds to `child_process`. The imports are read from raw source because the
module arrives as a string literal that masking would blank.

**Text that describes code is not code.** `pickle_loads`, `os_system`,
`subprocess_shell_true` and `new_function_call` match over the same masked source, so a docstring or a
comment naming the call does not fire. The two keyword secret kinds need their
name to be code, so `password = "..."` in a doctest is an example, not a
password, and `public_env_secret` ignores a comment naming the variable. The
vendor key shapes (`aws_access_key` and the rest) still match anywhere,
comments included, because a real key pasted into a comment is still a leak.
Prose files (`.md`, `.mdx`, `.rst`, `.txt`, `.adoc`) are scanned for secret
kinds only.

**Examples of a credential are not one.** A keyword value that is elided or
templated (`...`, a typographic ellipsis, `<your key>`, `your-`, `xxx`,
`example`, `placeholder`, `dummy`, `fake`, `changeme`), shorter than eight
characters, a short single-case word (`api_key="lmstudio"`, the dummy a local
server's client requires), or a CSS custom property (`--chart-1`) is skipped.

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

**False positives are still possible.** `weak_hash` fires on the word `md5`
anywhere, including in a comment explaining why md5 was removed. The high
kinds are tuned the other way: a `high` should be something to act on, so a
match that is only probably real is dropped.

**Two of them are worth knowing before you read a report.**

`exec` is not a global in JavaScript; the name belongs to `RegExp.prototype.exec`,
so the receiver-chain prefix in the pattern would otherwise match
`re.exec(expr)`, `/x/.exec(s)` and `cellPattern.exec(xml)` (ordinary parsing
code) at `high`. Outside Python the kind is resolved through the file's own
`child_process` bindings, which is where the dangerous call comes from: a
file's own function named `exec`, or a regex `.exec` beside a `spawn` import,
does not fire.

The remaining per-line patterns (`weak_hash`, the SQL kinds, the TLS kinds,
`unsafe_inner_html`) run on raw source, comments included,
so `weak_hash` still fires on a comment explaining why md5 was removed.

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
`med`, `low`). A finding under a test, fixture, spec, mock or example path is
`low` (see above), so under the default it shows as a warning and does not
fail; pass `--fail-on low` to fail on those too. Exit codes are the ones every
repowise CI gate uses: `0` passed,
`1` failed, `2` could not evaluate (not a git repository, unknown revision, no
merge-base in a shallow clone, a commit of the change cut off by a shallow
clone, an unreadable baseline). A git error is never read as a clean change.

Every format carries the masked snippet only, because CI logs are often
public: `table`, `json`, `markdown`, `github` (annotations plus the job
summary), `sarif` (for code-scanning upload) and `gitlab` (a GitLab Code
Quality report for the merge request widget). The gate is the registry
above and has its limits: a pass means no pattern matched a changed line.

**Baseline.** `--write-baseline FILE` records the change's findings in a
committed JSON file and exits 0; `--baseline FILE` accepts what it lists, so
only new findings fail. A finding is keyed on its file, kind and masked
matched line, so it stays accepted when an edit above it shifts its line
number. The vendor key shapes mask to their fixed prefix (`AKIA****`), so their
key also takes a one-way hash of the matched text: a different key on an
identical line is a new finding. The file never holds a raw value. The keyword
kinds (`hardcoded_password`, `hardcoded_secret`) stay keyed on the masked line
alone, because a short password could be guessed back from a hash of it; a
different value sharing the first four characters on an identical line stays
accepted. Because the gate sees one change at a time, writing to an existing
baseline adds to its entries (and to those of `--baseline`, when given)
instead of replacing them; remove an entry by deleting it from the file.
`--format sarif` marks accepted findings as suppressed; `--format gitlab`
leaves them out, because that format has no suppression field. In the Code
Quality report a finding at or above `--fail-on` is `critical` when high and
`major` otherwise, and one below it `minor`.

Without a REVSPEC the base comes from the CI's pull-request variables, else
the remote's default branch. A shallow checkout cannot be read commit by
commit, so fetch the full history; the gate exits 2 rather than guess. The
GitHub Action, the GitLab template and the SARIF upload are in
[Repowise in CI](../start/CI.md).

### Silencing one finding: `repowise-security-ignore`

For a single false positive, put `repowise-security-ignore` anywhere on the
finding's own line, in whatever comment syntax the file uses:

```python
EXAMPLE_KEY = "AKIAIOSFODNN7EXAMPLE"  # repowise-security-ignore: aws_access_key
```

How the marker is read:

- The token is exact and case-sensitive. `repowise-security-ignore-file` or
  `-next-line` is not a marker: there is no next-line or whole-file form,
  because a file-wide ignore for secrets is too blunt; use the baseline for that.
- The bare token silences every kind on its line.
- `repowise-security-ignore: kind, kind` silences only the kinds it lists (a
  custom pattern is `custom:<name>`). The first word after the colon and any
  comma-separated words after it are read as kind names, so free text there
  (`: see ticket`) names no real kind and silences nothing: the marker fails
  closed.
- It covers its own line only. For a secret committed inside the change, the
  marker has to be on the line in the commit that added it: adding it in a later
  commit silences the head but the earlier commit still reports the secret,
  because it stays in the history. Squash the change, or rotate the secret.

A silenced finding never fails the gate and never enters `--write-baseline`,
but it is not dropped: JSON lists it under `gate.suppressed` (file, line, kind,
severity, fingerprint, and the commit for a secret found in an earlier commit;
never the matched text) with `gate.suppressed_count`, the table and markdown
count and list it, SARIF carries it with an `inSource` suppression, and
`github` counts it in the job summary. The GitLab Code Quality report leaves it
out, as it does baselined findings, and the count goes to the job log.

### Custom patterns: `security.patterns`

A secret shape of your own goes in `.repowise/config.yaml`:

```yaml
security:
  patterns:
    - name: internal_token        # 1 to 40 of a-z, 0-9, _ and -, unique
      regex: 'itk_[A-Za-z0-9]{32}'
      severity: high              # low, med or high; high when omitted
```

Each pattern is a secret kind named `custom:<name>`, treated like the built-in
ones: only changed lines count, and every commit of the change is scanned for
it. The whole match is masked to its first four characters and `****` in every
format, including in the snippet of a built-in finding on the same line. The
finding is keyed on its file, kind and masked line, like the keyword kinds,
because a repository's own pattern can be as weak as a password; two matches
whose visible prefix and line agree share one baseline entry. Unlike the
built-in secrets, a custom pattern keeps its configured severity under test,
fixture and example paths. SARIF gets one rule per pattern (`custom/<name>`)
and the GitLab report the same check name.

The config is checked before the scan, and the gate exits 2
(`config_invalid`) listing every problem: an unknown key, a missing name or
regex, a duplicate or badly shaped name, a regex that does not compile, one
that can match zero characters (the empty string, `\b`, a lone lookahead), a
repeated group that itself repeats or alternates (`(a+)+`, `(\w*)*`,
`(a|aa)+`, which backtrack exponentially; the check is best effort), and a bad
severity. At most 50 patterns, each regex at most 500 characters. Patterns run
line by line and a line longer than 4096 characters is not matched against
them; the output counts such lines (`long_lines_skipped` in JSON, a run
property in SARIF, a note in the table, markdown and GitLab job log). Only line
length is bounded: a pathological regex that passes the checks can still be
slow.

### Before a commit: `--staged`

`repowise security check --staged` checks what the next commit would record:
the staged changed lines, read from the index, so an unstaged edit does not
count. There is no history to scan yet. It takes no REVSPEC (a usage error,
exit 2). `security.patterns` is read from the working tree's
`.repowise/config.yaml`, not from the index.

`repowise hook install --security` adds a pre-commit hook that runs it. The
hook blocks the commit only on exit 1, a finding at or above `high`; when the
check cannot run (exit 2, including an unexpected error, or `repowise` is not
installed) it says why and lets the commit through, because a broken tool must
not stop anyone committing. It goes into an existing pre-commit script as a
marked block at the top, and `repowise hook status` reports it once installed.
Plain `repowise hook install` does not add it, and `repowise hook uninstall`
removes everything repowise installed: the post-commit hook and this block,
leaving the rest of the script alone. Skip it once with
`git commit --no-verify`.

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
| `severity` | `high`, `med`, `low`: fixed per pattern, except a finding in test material is `low`. The Overview reads `med` as `medium` |
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
