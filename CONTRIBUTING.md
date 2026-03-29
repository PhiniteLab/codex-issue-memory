# Contributing

Thanks for considering a contribution to `codex-issue-memory`.

This repository is a local-first Python MCP tool backed by SQLite. The most useful contributions are the ones that improve correctness, clarity, install reliability, and long-term maintainability without inflating the project.

## Good contribution areas

- matching and ranking improvements
- duplicate-control or merge heuristics
- better install and verification reliability
- WSL and Linux documentation improvements
- tests for recurring engineering failure patterns
- backup, restore, and operational hardening

## Development setup

Clone the repository and create a local development environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .[dev]
```

If you are missing Linux or WSL packages, check [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md).

## Before opening a PR

Please keep changes aligned with the current project goals:

- local-first
- Linux and WSL friendly
- small dependency surface
- explicit install and verification flow
- reusable issue patterns instead of noisy logs

Run the relevant validation before submitting:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m codex_issue_memory.maintenance smoke
```

If you changed installer behavior, also run a temp-directory install and then run the installed verification script from the resulting install root, for example:

```bash
bash /path/to/install/scripts/verify_install.sh
```

## PR guidance

Good pull requests are:

- scoped narrowly
- honest about tradeoffs
- backed by validation
- careful about filesystem paths and SQLite behavior
- careful not to break Linux or WSL assumptions without updating docs

Please include:

- what changed
- why it changed
- how you validated it
- any remaining risks or follow-up work

## Documentation contributions

Docs improvements are welcome, especially when they:

- reduce setup ambiguity
- separate required dependencies from optional ones
- clarify WSL behavior
- make install, smoke, and verification steps more reproducible

## Issue-memory quality bar

This repository is not trying to store every past mistake. It is trying to store reusable, verified engineering patterns.

When proposing logic changes, prefer:

- normalized summaries
- stable error-family labels
- stable root-cause labels
- compact prevention rules

And avoid:

- raw transcript dumping
- speculative write-back
- one-off typo memorization
