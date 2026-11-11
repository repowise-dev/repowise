# Security Policy

## Supported Versions

Security fixes go into the latest minor release line only. Older lines do not
receive backports; upgrade to the latest release to pick up a fix.

| Version | Supported |
|---------|-----------|
| 0.54.x (latest minor) | Yes |
| Older than 0.54 | No |

## Reporting a Vulnerability

If you discover a security vulnerability in Repowise, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please email **security@repowise.dev** with:

- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fix (optional)

We will acknowledge your report within **48 hours** and aim to provide a fix or mitigation within **7 days** for critical issues.

## Scope

The following are in scope:

- The `repowise` Python package (PyPI)
- The Repowise web UI
- The Repowise API server
- The MCP server
- GitHub Actions workflows in this repository

## Out of Scope

- Vulnerabilities in third-party dependencies (report these upstream, but let us know so we can update)
- Issues requiring physical access to the machine running Repowise

## Disclosure Policy

We follow coordinated disclosure. Once a fix is released, we will:

1. Credit the reporter (unless they prefer anonymity)
2. Publish a security advisory via GitHub Security Advisories
3. Release a patched version on PyPI
