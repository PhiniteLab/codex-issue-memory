# codex-issue-memory Plugin Wrapper Examples

## 1) Local plugin install (reference)

```bash
mkdir -p ~/.codex/local-plugins
cd ~/.codex/local-plugins

git clone https://github.com/PhiniteLab/codex-issue-memory.git codex-issue-memory

# keep ~/.codex/config.toml as the MCP authority
# restart Codex
```

## 2) `.mcp.json` remote/local templates

```json
{
  "mcpServers": {
    "issue-memory-remote-template": {
      "type": "http",
      "url": "https://example.invalid/mcp/issue-memory"
    },
    "issue-memory-local-command-template": {
      "type": "stdio",
      "command": "python3",
      "args": ["-m", "codex_issue_memory.server"]
    }
  }
}
```

## 3) Optional marketplace-style mapping

Use a marketplace entry that points to the local plugin folder only when your environment uses a marketplace bridge.

```json
{
  "plugins": [
    {
      "name": "codex-issue-memory",
      "source": {
        "source": "local",
        "path": "./plugins/codex-issue-memory"
      },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL"
      },
      "category": "Productivity"
    }
  ]
}
```
