# sample_repo

A deliberately small, multi-language repository used as a test fixture for repowise.

It contains real Python and TypeScript code with realistic module structure:
imports, classes, functions, type annotations, and inter-module dependencies.

## Structure

```
sample_repo/
├── python_pkg/          # Python: calculator, models, utils
│   ├── __init__.py
│   ├── calculator.py    # Core arithmetic operations
│   ├── models.py        # Data models (dataclasses)
│   └── utils.py         # Shared helper utilities
├── typescript_pkg/      # TypeScript: API client + types
│   ├── src/
│   │   ├── client.ts    # HTTP API client
│   │   ├── types.ts     # Shared TypeScript interfaces
│   │   └── utils.ts     # Utility functions
│   ├── package.json
│   └── tsconfig.json
├── Makefile
└── .gitignore
```

## Purpose

repowise runs its integration tests against this fixture:
- AST parser: extract all symbols from Python + TypeScript files
- Graph builder: build dependency graph, compute PageRank
- Change detector: diff between fixture versions
- Page generator: generate documentation pages (using MockProvider)
