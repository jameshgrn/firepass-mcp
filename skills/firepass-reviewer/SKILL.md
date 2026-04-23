---
name: firepass-reviewer
description: |
  Delegate code review tasks to FirePass (Kimi K2.5 Turbo via Fireworks AI).
  The reviewer has a read-only tool loop and returns structured review output:
  blocking issues, suggestions, and what's done well. Use for code review,
  PR review, or architecture review.
author: Jake Gearon
version: 1.0.0
---

# FirePass Reviewer

Autonomous code reviewer powered by Kimi K2.5 Turbo. Has its own read-only tool
loop with: read_file, ripgrep, ast_grep, jq, glob_find, tree, list_dir.
Returns structured review: blocking issues, suggestions, and what's done well.

## When to use

- Reviewing code changes or diffs
- PR review
- Architecture review
- Security audit of code paths
- Pre-merge quality checks

## Preference

Use for code review tasks where you want structured, opinionated output with
file:line citations and severity labels (bug, security, design, nit).

## How to use

Call `mcp__firepass__firepass_reviewer` with:
- `prompt`: What to review — files, a diff, a PR description, or a specific concern
- `cwd`: **Important** — set to the project/repo root
- `context`: Optional diff, file contents, or PR description to pre-load
- `max_iterations`: Tool-call rounds (default 60)

## Output format

The reviewer structures its output as:
- **Summary**: 1-2 sentence overall assessment
- **Blocking**: Issues that must be fixed (bug, security, correctness)
- **Suggestions**: Non-blocking improvements (design, performance, style)
- **Good**: What's done well

All items cite file:line references.

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
