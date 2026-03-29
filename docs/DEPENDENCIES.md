# Dependencies

This page separates system-level prerequisites from Python-level package dependencies so users can install the repository with fewer surprises.

## Python package dependencies

Confirmed from `pyproject.toml` and `requirements.txt`:

### Runtime

- `mcp[cli]>=1.0.0,<2.0.0`

### Development

- `pytest>=8.0.0`

Install the development environment from a checkout with:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .[dev]
```

## Python standard-library dependencies

No extra pip package is required for SQLite itself.

The code uses Python's built-in standard library modules for:

- `sqlite3`
- `argparse`
- `pathlib`
- `tempfile`
- `json`
- `hashlib`
- `shutil`

## Required system dependencies

For the documented install path, users should have:

- `git`
- `bash`
- `python3`
- `python3-venv`
- `python3-pip`

## Optional system dependencies

These are not strictly required for the core server, but they improve the experience:

- `rsync`
  - used by `install.sh` when available for cleaner install-root syncs
- `cron` or `crontab`
  - needed only for scheduled backups
- `sqlite3`
  - useful only for manual database inspection from the terminal
- `sudo`
  - only relevant if you want the cron helper to try enabling or starting the cron service

## Recommended Ubuntu or WSL package install

For Ubuntu-based Linux or WSL environments:

```bash
sudo apt update
sudo apt install -y git bash python3 python3-venv python3-pip
```

Optional extras:

```bash
sudo apt install -y rsync cron sqlite3
```

## Dependency notes

- The installer creates its own virtual environment and installs Python packages there.
- The runtime dependency surface is intentionally small.
- The install contract is broader than `requirements.txt` alone because the repository also relies on shell tooling and a Linux or WSL-style environment.
