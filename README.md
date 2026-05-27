# firepass-mcp

MCP server that turns [Kimi K2.6 Turbo](https://fireworks.ai) into an agentic coding assistant. The model gets a tool loop — it can read/write files, run shell commands, and search code with ripgrep, ast-grep, jq, and glob — and iterates autonomously until the task is done.

Four tools exposed over MCP:

| Tool | Capabilities | Use case |
|------|-------------|----------|
| `firepass_worker` | read_file, write_file, edit_file, bash, ripgrep, glob_find, ast_grep, jq, list_dir, tree, done | Coding, refactoring, bug fixes |
| `firepass_researcher` | read_file, ripgrep, glob_find, ast_grep, jq, list_dir, tree, done (read-only) | Code analysis, architecture review |
| `firepass_reviewer` | read_file, ripgrep, glob_find, ast_grep, jq, list_dir, tree, done (read-only) | Code review with structured output |
| `firepass_trio` | researcher → worker → reviewer chain with bounded fix loop-back | Plan-then-implement-then-review in one MCP call |

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

### Codex CLI

Add the server with:

```bash
codex mcp add firepass --env FIREWORKS_API_KEY=fw-... -- uv run firepass-mcp
```

This writes a config like:

```toml
[mcp_servers.firepass]
command = "uv"
args = ["run", "firepass-mcp"]

[mcp_servers.firepass.env]
FIREWORKS_API_KEY = "fw-..."
```

### Claude Code

Add the server with:

```bash
claude mcp add -e FIREWORKS_API_KEY=fw-... firepass -- uv run firepass-mcp
```

This writes a config like:

```json
{
  "mcpServers": {
    "firepass": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "firepass-mcp"],
      "env": {
        "FIREWORKS_API_KEY": "fw-..."
      }
    }
  }
}
```

### Claude Desktop / Generic MCP JSON

If your client reads MCP JSON directly, use:

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

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FIREWORKS_API_KEY` | (required) | Fireworks AI API key |
| `FIREPASS_MODEL` | `accounts/fireworks/routers/kimi-k2p6-turbo` | Model ID |
| `FIREPASS_BASH_TIMEOUT` | `60` | Shell command timeout (seconds) |
| `FIREPASS_MAX_OUTPUT` | `50000` | Max chars per tool result |
| `FIREPASS_MAX_READ` | `100000` | Max chars per file read |

## How it works

1. You call `firepass_worker`, `firepass_researcher`, `firepass_reviewer`, or `firepass_trio` with a prompt and a required `cwd`
2. The server (`server.py`) sends the prompt to Kimi K2.6 Turbo with function-calling enabled, using `tools.py` for the typed ToolSpec registry and executors and `messages.py` for context budgeting
3. The model explores the codebase, makes edits, runs tests, and iterates
4. Every tool has a frozen-dataclass argument contract with `additionalProperties: false` enforced at runtime — unknown fields are rejected
5. When done, it calls `done()` with an executive summary
6. The summary (plus an activity log) is returned as the tool result

All roles get 60 iterations by default (capped at 200), configurable per call.

`firepass_trio` chains researcher, worker, and reviewer: the researcher gathers context, the worker implements, and the reviewer audits the result. The reviewer can send the worker back for fixes up to `max_review_rounds` times (default 2, capped at 5). The response is an XML envelope that contains each sub-result as a separate tag so the calling LLM can address them individually.

For Fireworks rate-limit behavior, worker fan-out guidance, and the recommended path before running many parallel workers, see [docs/fireworks-scaling.md](docs/fireworks-scaling.md).

### Response format

Every tool result is returned as an XML envelope so the calling LLM can read sub-results structurally.

Single tool (e.g. `firepass_worker`):

```xml
<firepass_worker status="completed" iterations="4" tool_calls="3">
  <result>Done: refactored auth logic into helpers.py</result>
  <activity>
    <call>read_file(path="src/auth.py")</call>
    <call>write_file(path="src/helpers.py", content="...")</call>
    <call>done(result="Done: refactored auth logic into helpers.py")</call>
  </activity>
</firepass_worker>
```

Trio call (`firepass_trio`):

```xml
<firepass_trio status="approved" rounds="1">
  <research status="completed" iterations="3" tool_calls="2">...</research>
  <rounds>
    <round n="1">
      <implementation status="completed" iterations="5" tool_calls="4">...</implementation>
      <review status="completed" iterations="2" tool_calls="1">...</review>
    </round>
  </rounds>
</firepass_trio>
```

## Security model

All file operations (`read_file`, `write_file`, `edit_file`, `glob_find`, `ripgrep`, `ast_grep`, `jq`, `tree`, `list_dir`) are sandboxed to the required `cwd` you provide. Paths are resolved and validated against the working directory before any I/O.

The **researcher** and **reviewer** are read-only — `bash`, `write_file`, and `edit_file` are blocked both at the API schema level (model never sees them) and at runtime (server rejects them even if hallucinated). Dangerous ripgrep flags (`--pre`, `--pre-glob`, `--search-zip`, `--replace`, `-r`, `-z`) are also blocked.

The **worker** has full access including `bash`. It is not sandboxed at the command level — treat it like giving shell access to a remote developer scoped to your project directory.

**Limits:**
- File writes capped at 1 MB per operation
- File reads capped at 100K characters
- Tool output capped at 50K characters
- Context budget of 200K characters. Phase 1 truncates oldest tool outputs to `[truncated]`; phase 2 compacts assistant tool_call arguments to `{}`. If still over budget, an error is raised rather than silently exceeding.
- Configurable iteration limits (default 60 for all roles, capped at 200)
- Review rounds capped at 5 in the trio (default 2)

## Development

Install dev dependencies and run tests:

```bash
uv sync
uv run pytest -q tests/test_server.py
```

Lint and type-check:

```bash
uv run ruff check src tests
uv run ty check src
```

## License

MIT
