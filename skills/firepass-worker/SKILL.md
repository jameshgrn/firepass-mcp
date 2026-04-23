---
name: firepass-worker
description: |
  Delegate coding/implementation tasks to FirePass (Kimi K2.5 Turbo via Fireworks AI).
  The worker has its own tool loop — it can read/write/edit files, run bash, search
  with ripgrep/ast-grep/jq, and iterate autonomously. Use INSTEAD OF default Agent
  subagents for coding work.
author: Jake Gearon
version: 2.1.0
---

# FirePass Worker

Autonomous coding agent powered by Kimi K2.5 Turbo. Has its own tool loop with:
read_file, write_file, edit_file, bash, ripgrep, ast_grep, jq, glob_find, tree, list_dir.

## When to use

- Writing new code or implementations
- Fixing bugs
- Refactoring existing code
- Implementing features from a spec
- Running tests + fixing failures iteratively

## Preference

Use INSTEAD OF the default Agent subagent for coding tasks.

## How to use

Call `mcp__firepass__firepass_worker` with:
- `prompt`: Clear description of the coding task
- `cwd`: **Important** — set to the project root so the agent can find files
- `context`: Optional pre-loaded file contents, error messages, or specs
- `max_iterations`: Tool-call rounds (default 60)

## Capabilities

| Tool | Description |
|------|-------------|
| read_file | Read file contents with line numbers |
| write_file | Create or overwrite files (max 1MB) |
| edit_file | Replace exact text in a file (must match once) |
| bash | Run shell commands (configurable timeout, default 60s) |
| ripgrep | Fast regex search via rg (dangerous flags blocked) |
| glob_find | Find files by glob pattern |
| ast_grep | Structural code search via sg |
| jq | Query/transform JSON |
| list_dir | List directory contents with sizes |
| tree | Directory tree (excludes .git, __pycache__, node_modules, .venv) |

## Security

All file operations are sandboxed to the provided `cwd`. The worker has full
shell access — treat it like giving shell access to a remote developer scoped
to your project directory.
