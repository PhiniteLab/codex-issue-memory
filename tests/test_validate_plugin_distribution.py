from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path


def _load_validator_module():
    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "validate_plugin_distribution.py"
    )
    module_name = "validate_plugin_distribution"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


validate_module = _load_validator_module()


def test_validate_plugin_distribution_happy_path(tmp_path: Path) -> None:
    package_root = tmp_path / "src" / "codex_issue_memory"
    package_root.mkdir(parents=True)
    plugin_root = tmp_path / ".codex-plugin"
    plugin_root.mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (package_root / "__init__.py").write_text(
        '__version__ = "0.1.0"\n', encoding="utf-8"
    )
    (plugin_root / "plugin.json").write_text(
        json.dumps(
            {
                "name": "codex-issue-memory",
                "version": "0.1.0",
                "description": "Test description",
                "mcpServers": "./.mcp.json",
                "interface": {
                    "displayName": "codex-issue-memory",
                    "shortDescription": "short",
                    "longDescription": "long",
                    "developerName": "Mehmet",
                    "category": "Productivity",
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "issue-memory-local-command-template": {
                        "type": "stdio",
                        "command": "python3",
                        "args": ["-m", "codex_issue_memory.server"],
                    }
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "\n".join(
            [
                "# Test Project",
                "## Install Mode A — Standard MCP install",
                "## Install Mode B — Plugin-wrapper install",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = validate_module.validate_plugin_distribution(tmp_path)
    assert report.passed is True
    assert report.failures == []


def test_validate_plugin_distribution_flags_missing_interface_field(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "src" / "codex_issue_memory"
    package_root.mkdir(parents=True)
    plugin_root = tmp_path / ".codex-plugin"
    plugin_root.mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (package_root / "__init__.py").write_text(
        '__version__ = "0.1.0"\n', encoding="utf-8"
    )
    (plugin_root / "plugin.json").write_text(
        json.dumps(
            {
                "name": "codex-issue-memory",
                "version": "0.1.0",
                "description": "Test description",
                "mcpServers": "./.mcp.json",
                "interface": {
                    "displayName": "codex-issue-memory",
                    "shortDescription": "short",
                    "longDescription": "long",
                    "category": "Productivity",
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".mcp.json").write_text("{}", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "## Install Mode A — Standard MCP install\n## Install Mode B — Plugin-wrapper install\n",
        encoding="utf-8",
    )

    report = validate_module.validate_plugin_distribution(tmp_path)
    assert report.passed is False
    assert any("developerName" in failure for failure in report.failures)


def test_validate_plugin_distribution_repo_root_passes() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    report = validate_module.validate_plugin_distribution(repo_root)
    assert report.passed is True
