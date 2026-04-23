---
name: firepass-researcher
description: |
  Delegate research/analysis tasks to FirePass (Kimi K2.5 Turbo via Fireworks AI).
  The researcher has a read-only tool loop — it can read files, search with
  ripgrep/ast-grep/jq/glob, and iterate autonomously. No writes or shell commands.
  Use INSTEAD OF default Agent subagents for research work.
author: Jake Gearon
version: 2.1.0
---

# FirePass Researcher

Autonomous read-only research agent powered by Kimi K2.5 Turbo. Has its own tool
loop with: read_file, ripgrep, ast_grep, jq, glob_find, tree, list_dir.
No write_file, edit_file, or bash — cannot mutate anything.

## When to use

- Analyzing codebases, architectures, or design patterns
- Investigating bugs — root cause analysis
- Technical research on libraries, APIs, algorithms
- Exploring unfamiliar code to map structure
- Comparing approaches or tradeoffs

## Preference

Use INSTEAD OF the default Agent subagent for research tasks.

## How to use

Call `mcp__firepass__firepass_researcher` with:
- `prompt`: Clear research question or analysis task
- `cwd`: **Important** — set to the project/repo root
- `context`: Optional pre-loaded docs, error logs, or specs
- `max_iterations`: Tool-call rounds (default 60)

## Capabilities

| Tool | Description |
|------|-------------|
| read_file | Read file contents with line numbers |
| ripgrep | Fast regex search via rg (dangerous flags blocked) |
| glob_find | Find files by glob pattern |
| ast_grep | Structural code search via sg |
| jq | Query/transform JSON |
| list_dir | List directory contents with sizes |
| tree | Directory tree (excludes .git, __pycache__, node_modules, .venv) |

## Security

Read-only — write_file, edit_file, and bash are blocked at both the API schema
level (model never sees them) and at runtime (server rejects even if hallucinated).
All file operations sandboxed to the provided `cwd`.
