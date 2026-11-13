---
name: codebase-questions
description: Answer questions about a repository indexed on repowise.dev with the Repowise tools. Use when the user asks what a repository contains, how its code works, where something is defined, how risky a change is, or about its code health, dead code or security findings.
---

# Answer questions about an indexed repository

Repowise reads the index that repowise.dev builds for each repository: the
dependency graph, git history, ownership, code health and generated
documentation. Every tool is read-only.

## 1. Pick the repository

- If the user names a repository as `owner/name`, pass it as `repo`.
- If the user means the open workspace, read its GitHub remote
  (`git remote get-url origin`) and pass that `owner/name`.
- Otherwise call `list_repos`. If it returns one indexed repository, use it.
  If it returns several, ask the user which one.
- If `list_repos` returns nothing, tell the user to index a repository from
  their dashboard at https://repowise.dev/dashboard, then ask again.

## 2. Pick the tool

| The user wants | Tool |
|---|---|
| What the repository is and how it is laid out | `get_overview` |
| How something works end to end | `get_answer` |
| A symbol, file path or literal | `search_codebase`, then `get_symbol` for one symbol |
| Callers, callees and context for a file or symbol | `get_context` |
| How risky it is to edit files | `get_risk` |
| The risk of a commit that is already indexed | `get_change_risk` |
| Why code is shaped the way it is | `get_why` |
| Code health scores and findings | `get_health` |
| Unused code | `get_dead_code` |
| Dependency CVEs and exposed secrets | `get_security` |

Ask one question per `get_answer` call. Start with the narrowest tool that
answers the question. Before editing a file, call `get_risk` on it and read the
files it names as usually changing together.

## 3. Answer

- Cite the file paths, symbols and line ranges the tools return, then open
  those files in the workspace when the task needs the exact code.
- The index reflects the last indexed commit. When the local checkout is ahead
  of it, trust the local file for its current contents.
- Do not invent files, owners or numbers that the tools did not return.

## 4. Handle limits plainly

- If a tool says the repository is not found or not accessible, say that it is
  not indexed on repowise.dev or not shared with this account. Do not guess
  what it contains.
- If a tool says a feature is not available for the account, say so in one
  sentence and do not retry.
