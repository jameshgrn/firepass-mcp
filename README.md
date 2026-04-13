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
uvx firepass-mcp
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

## Security model

All file operations (`read_file`, `write_file`, `edit_file`, `glob_find`, `ripgrep`, `ast_grep`, `jq`, `tree`, `list_dir`) are sandboxed to the `cwd` you provide. Paths are resolved and validated against the working directory before any I/O.

The **researcher** is read-only — `bash`, `write_file`, and `edit_file` are blocked both at the API schema level (model never sees them) and at runtime (server rejects them even if hallucinated). Dangerous ripgrep flags (`--pre`, `--replace`, `-z`) are also blocked.

The **worker** has full access including `bash`. It is not sandboxed at the command level — treat it like giving shell access to a remote developer scoped to your project directory.

**Limits:**
- File writes capped at 1 MB per operation
- File reads capped at 100K characters
- Tool output capped at 50K characters
- Context budget of 200K characters (old tool results truncated when exceeded)
- Configurable iteration limits (default 50 worker, 30 researcher)

## License

MIT
