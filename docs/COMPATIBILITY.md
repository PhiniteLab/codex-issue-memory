# Compatibility

This repository is intentionally designed around Linux-style local tooling and filesystem layouts.

## Support matrix

| Environment | Status | Notes |
| --- | --- | --- |
| Linux | Supported | Primary target for the installer, runtime paths, and backup flow. |
| WSL 2 | Supported | Explicitly supported and appropriate for this repository's Linux-oriented install flow. |
| Windows without WSL | Not supported | The current installer and path conventions assume `bash`, Linux-style paths, and a Python virtualenv layout under a POSIX shell. |
| macOS | Unverified | It may be possible with manual adjustments, but this repository does not currently document or promise it. |

## Tested-environment note

The maintainer reports that this project runs on their local machine and on WSL. The repository itself is also structured around that reality:

- `install.sh` assumes a bash-based environment
- default paths use Linux home-directory conventions
- backup guidance recommends keeping the live SQLite database inside Linux or WSL storage

## WSL guidance

If you are using WSL, the recommended setup is:

- keep the repository checkout inside the Linux filesystem
- keep the live SQLite database under `~/.local/share/codex-issue-memory`
- use `/mnt/c/...` only for mirrored backups, not for the active database

This project is a good fit for WSL because:

- the installer is bash-first
- the runtime paths are POSIX-friendly
- Codex and MCP workflows often behave more predictably in a Linux-like shell environment

## WSL and cron

Scheduled backups rely on `crontab`. On WSL, that usually means:

- installing cron explicitly
- optionally enabling `systemd` if your WSL setup needs it for service management

The example [wsl.conf template](../templates/wsl.conf.example) is included for this reason.

If you do not want scheduled backups yet, install with:

```bash
SKIP_CRON_INSTALL=1 bash install.sh
```

You can still run manual backups later with:

```bash
issue-memory-maint backup
```

## Non-goals

This repository does not currently try to be:

- a native Windows desktop installer
- a cross-platform GUI application
- a cloud-hosted memory service

It is a local-first Linux and WSL developer tool.
