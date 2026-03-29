# Support

## What this repository supports

Primary supported environments:

- Linux
- WSL 2

The repository is aimed at:

- Codex users
- MCP-based local tooling workflows
- local SQLite-backed issue memory

## Before opening an issue

Please check:

1. [docs/INSTALLATION.md](docs/INSTALLATION.md)
2. [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)
3. [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md)
4. [docs/OPERATIONS.md](docs/OPERATIONS.md)

Then run the relevant local checks:

```bash
bash /path/to/install/scripts/verify_install.sh
```

or from a development checkout:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m codex_issue_memory.maintenance smoke
```

## When asking for help

Please include:

- Linux or WSL
- Python version
- how you installed the repo
- the exact failing command
- the shortest meaningful error excerpt
- whether the problem is install, runtime, backup, or matching-related

## What to expect

Good support requests are the ones that are reproducible and concrete.

The easiest issues to help with are:

- install failures
- WSL path and environment problems
- Codex config registration problems
- smoke-test failures
- matching regressions backed by test cases

## What this repository does not currently promise

- native Windows support without WSL
- managed cloud hosting
- enterprise SLA-style response times
