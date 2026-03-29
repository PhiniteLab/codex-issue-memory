# Security Policy

## Scope

`codex-issue-memory` is a local-first developer tool that:

- stores issue patterns in a local SQLite database
- updates local Codex configuration files
- can install a scheduled backup job

Security-sensitive areas include:

- config mutation under `~/.codex`
- local SQLite data handling
- backup and mirror paths
- shell-script install behavior

## Reporting a vulnerability

Please do not open a public GitHub issue for a security-sensitive finding that includes exploit details, secrets, or unsafe reproduction steps.

Preferred reporting path:

1. Use GitHub's private vulnerability reporting flow for the repository if it is enabled.
2. If that is not available, contact the maintainer through a private channel associated with the repository before public disclosure.

## What to include

Helpful reports include:

- affected version or commit
- operating environment such as Linux or WSL
- exact command or install path involved
- impact summary
- minimal reproduction steps
- whether the issue touches config files, backup paths, or SQLite data

## Disclosure expectations

Please give the maintainer reasonable time to reproduce and patch the issue before publishing full details.

## Current support stance

This repository is currently documented for Linux and WSL workflows. Native Windows without WSL is not a supported target, so platform-specific behavior outside the documented environment may not receive the same response speed.
