# firepass-mcp

MCP server that turns [Kimi K2.5 Turbo](https://fireworks.ai) into an agentic coding assistant. The model gets a tool loop — it can read/write files, run shell commands, and search code with ripgrep, ast-grep, jq, and glob — and iterates autonomously until the task is done.

Two tools exposed over MCP:

| Tool | Capabilities | Use case |
|------|-------------|----------|
| `firepass_worker` | read, write, edit, bash, ripgrep, ast-grep, jq, glob, tree | Coding, refactoring, bug fixes |
| `firepass_researcher` | read, ripgrep, ast-grep, jq, glob, tree (read-only) | Code analysis, architecture review |

## Requirements

- Python 3.10+
- A [Fireworks AI](https://fireworks.ai) API key
- `rg` (ripgrep), `sg` (ast-grep), `jq`, `tree` on PATH for full tool coverage
- `bash`, `ls` (standard on POSIX systems)

## Install

```bash
# uvx (zero-install, recommended)
uvx firepass-mcp

# or pip
pip install firepass-mcp
```

## Configuration

Set your API key:

```bash
export FIREWORKS_API_KEY="fw-..."
```

### Claude Code / Claude Desktop

Add to your MCP config (`~/.mcp.json` or Claude Desktop settings):

```json
{
  "mcpServers": {
    "firepass": {
      "command": "uvx",
      "args": ["firepass-mcp"],
      "env": {
        "FIREWORKS_API_KEY": "fw-..."
      }
    }
  }
}
```

### Any MCP client

```json
{
  "firepass": {
    "command": "firepass-mcp"
  }
}
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FIREWORKS_API_KEY` | (required) | Fireworks AI API key |
| `FIREPASS_MODEL` | `accounts/fireworks/routers/kimi-k2p5-turbo` | Model ID |
| `FIREPASS_BASH_TIMEOUT` | `60` | Shell command timeout (seconds) |
| `FIREPASS_MAX_OUTPUT` | `50000` | Max chars per tool result |
| `FIREPASS_MAX_READ` | `100000` | Max chars per file read |

## How it works

1. You call `firepass_worker` or `firepass_researcher` with a prompt and working directory
2. The server sends the prompt to Kimi K2.5 Turbo with function-calling enabled
3. The model explores the codebase, makes edits, runs tests, and iterates
4. When done, it calls `done()` with an executive summary
5. The summary (plus an activity log) is returned as the tool result

The worker gets 50 iterations by default; the researcher gets 30. Both are configurable per call.

## License

MIT
