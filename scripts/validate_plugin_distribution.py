from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

EXPECTED_PLUGIN_NAME = "codex-issue-memory"
EXPECTED_MCP_SERVERS_REF = "./.mcp.json"

REQUIRED_TOP_LEVEL_FIELDS: tuple[str, ...] = (
    "name",
    "version",
    "description",
    "mcpServers",
    "interface",
)

REQUIRED_INTERFACE_FIELDS: tuple[str, ...] = (
    "displayName",
    "shortDescription",
    "longDescription",
    "developerName",
    "category",
)


@dataclass
class ValidationResult:
    """Holds a compact, reusable validation result."""

    passed: bool
    checks: list[str]
    failures: list[str]


def _extract_version(path: Path, pattern: str) -> tuple[bool, str | None, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, None, f"Failed to read {path}: {exc}"
    match = re.search(pattern, raw, re.MULTILINE)
    if not match:
        return False, None, f"Could not extract version from {path}"
    return True, match.group(1), ""


def _load_json(path: Path) -> tuple[bool, dict | list | None, str]:
    """Load JSON from path and return (ok, payload, message)."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except OSError as exc:
        return False, None, f"Failed to read {path}: {exc}"
    except json.JSONDecodeError as exc:
        return False, None, f"Invalid JSON in {path}: {exc}"
    return True, payload, ""


def _has_heading(path: Path, heading: str) -> bool:
    marker = heading.lower()
    for line in path.read_text(encoding="utf-8").splitlines():
        normalized = line.strip().lower()
        if normalized.startswith("#") and marker in normalized:
            return True
    return False


def _check(condition: bool, message_ok: str, message_fail: str, checks: list[str], failures: list[str]) -> None:
    checks.append(f"{'OK' if condition else 'FAIL'}: {message_ok if condition else message_fail}")
    if not condition:
        failures.append(message_fail)


def validate_plugin_distribution(
    project_root: Path,
    *,
    readme_filename: str = "README.md",
) -> ValidationResult:
    """Validate plugin-wrapper distribution files in a project tree."""

    project_root = project_root.expanduser()
    plugin_path = project_root / ".codex-plugin" / "plugin.json"
    mcp_path = project_root / ".mcp.json"
    readme_path = project_root / readme_filename
    pyproject_path = project_root / "pyproject.toml"
    package_init_path = project_root / "src" / "codex_issue_memory" / "__init__.py"

    checks: list[str] = []
    failures: list[str] = []
    plugin_ok = False
    plugin_payload: dict | list | None = None

    _check(
        plugin_path.is_file(),
        f"{plugin_path} exists",
        f"Missing {plugin_path}",
        checks,
        failures,
    )
    if plugin_path.is_file():
        plugin_ok, plugin_payload, plugin_error = _load_json(plugin_path)
        _check(plugin_ok, f"{plugin_path} is valid JSON", plugin_error, checks, failures)
        if plugin_ok and isinstance(plugin_payload, dict):
            missing_top = [field for field in REQUIRED_TOP_LEVEL_FIELDS if field not in plugin_payload]
            _check(
                not missing_top,
                "plugin.json required top-level fields exist",
                f"plugin.json missing required top-level fields: {', '.join(missing_top)}",
                checks,
                failures,
            )

            interface = plugin_payload.get("interface", {})
            _check(
                isinstance(interface, dict),
                "plugin.json interface is a JSON object",
                "plugin.json interface field must be an object",
                checks,
                failures,
            )

            _check(
                plugin_payload.get("name") == EXPECTED_PLUGIN_NAME,
                f"plugin.json name is {EXPECTED_PLUGIN_NAME}",
                f"plugin.json name must be {EXPECTED_PLUGIN_NAME}",
                checks,
                failures,
            )
            _check(
                plugin_payload.get("mcpServers") == EXPECTED_MCP_SERVERS_REF,
                f"plugin.json mcpServers points to {EXPECTED_MCP_SERVERS_REF}",
                f"plugin.json mcpServers must point to {EXPECTED_MCP_SERVERS_REF}",
                checks,
                failures,
            )

            if isinstance(interface, dict):
                missing_interface = [
                    field for field in REQUIRED_INTERFACE_FIELDS if field not in interface
                ]
                _check(
                    not missing_interface,
                    "plugin.json interface subfields exist",
                    (
                        "plugin.json interface missing required subfields: "
                        f"{', '.join(missing_interface)}"
                    ),
                    checks,
                    failures,
                )
        else:
            if not isinstance(plugin_payload, dict):
                _check(
                    False,
                    "",
                    f"{plugin_path} must be a JSON object",
                    checks,
                    failures,
                )

    _check(
        mcp_path.is_file(),
        f"{mcp_path} exists",
        f"Missing {mcp_path}",
        checks,
        failures,
    )
    if mcp_path.is_file():
        mcp_ok, _, mcp_error = _load_json(mcp_path)
        _check(
            mcp_ok,
            f"{mcp_path} is valid JSON",
            mcp_error,
            checks,
            failures,
        )
        if mcp_ok:
            mcp_payload_ok, mcp_payload, _ = _load_json(mcp_path)
            if mcp_payload_ok:
                _check(
                    isinstance(mcp_payload, dict) and isinstance(mcp_payload.get("mcpServers"), dict),
                    ".mcp.json contains an mcpServers object",
                    ".mcp.json must contain an mcpServers object",
                    checks,
                    failures,
                )

    _check(
        readme_path.is_file(),
        f"{readme_path} exists",
        f"Missing {readme_path}",
        checks,
        failures,
    )
    if readme_path.is_file():
        _check(
            _has_heading(readme_path, "install mode a"),
            "README contains Install Mode A",
            "README is missing Install Mode A heading",
            checks,
            failures,
        )
        _check(
            _has_heading(readme_path, "install mode b"),
            "README contains Install Mode B",
            "README is missing Install Mode B heading",
            checks,
            failures,
        )

    _check(
        pyproject_path.is_file(),
        f"{pyproject_path} exists",
        f"Missing {pyproject_path}",
        checks,
        failures,
    )
    _check(
        package_init_path.is_file(),
        f"{package_init_path} exists",
        f"Missing {package_init_path}",
        checks,
        failures,
    )
    if pyproject_path.is_file() and package_init_path.is_file() and plugin_path.is_file():
        pyproject_ok, pyproject_version, pyproject_error = _extract_version(
            pyproject_path, r'^version\s*=\s*"([^"]+)"'
        )
        _check(
            pyproject_ok,
            "pyproject.toml version was extracted",
            pyproject_error,
            checks,
            failures,
        )
        init_ok, package_init_version, init_error = _extract_version(
            package_init_path, r'^__version__\s*=\s*"([^"]+)"'
        )
        _check(
            init_ok,
            "package __init__ version was extracted",
            init_error,
            checks,
            failures,
        )
        if pyproject_ok and init_ok and plugin_ok and isinstance(plugin_payload, dict):
            _check(
                plugin_payload.get("version") == pyproject_version == package_init_version,
                "plugin.json version matches project/package version",
                "plugin.json version must match pyproject.toml and src/codex_issue_memory/__init__.py",
                checks,
                failures,
            )

    return ValidationResult(passed=(len(failures) == 0), checks=checks, failures=failures)


def run_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate plugin distribution files.")
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root to validate (default: current directory).",
    )
    parser.add_argument(
        "--readme",
        default="README.md",
        help="README filename to validate mode headings against.",
    )
    args = parser.parse_args(argv)

    report = validate_plugin_distribution(Path(args.project_root), readme_filename=args.readme)

    print("Plugin distribution validation:")
    for check in report.checks:
        print(f" - {check}")

    if report.passed:
        print("Result: PASS")
        return 0

    print("Result: FAIL")
    print("Failures:")
    for failure in report.failures:
        print(f" - {failure}")
    return 1


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    sys.exit(main())
