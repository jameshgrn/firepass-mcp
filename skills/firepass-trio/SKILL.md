---
name: firepass-trio
description: |
  Delegate full plan→implement→review work to FirePass (Kimi K2.6 Turbo
  via Fireworks AI). The trio runs researcher, then worker, then
  reviewer in one MCP call, with a bounded fix loop-back when the
  reviewer says NEEDS-FIXES. Use INSTEAD OF stitching the three
  single-role tools yourself.
author: Jake Gearon
version: 1.0.0
---

# FirePass Trio

Autonomous end-to-end agent powered by Kimi K2.6 Turbo. Chains researcher → worker → reviewer in a single MCP call. If the reviewer finds issues and marks them NEEDS-FIXES, the trio loops back: the worker re-runs with reviewer feedback appended to context, then the reviewer re-runs. The loop is bounded by `max_review_rounds`.

## When to use

- End-to-end feature implementation with built-in audit
- Bug fixes that should be planned before coded
- Any change you'd otherwise call all three single-role tools for

## When NOT to use

- Pure research (use firepass-researcher)
- Pure implementation with no plan needed (use firepass-worker)
- Pure review of an existing diff (use firepass-reviewer)

## Preference

Use INSTEAD OF chaining the three single-role tools manually.

## How to use

Call `mcp__firepass__firepass_trio` with:
- `prompt`: Clear description of the coding task
- `cwd`: **Important** — set to the project root so the agent can find files
- `context`: Optional pre-loaded file contents, error messages, or specs
- `max_iterations`: Tool-call rounds per sub-agent (default 60, capped at 200)
- `max_review_rounds`: Fix loop rounds between worker and reviewer (default 2, capped at 5)

## Response shape

```xml
<firepass_trio status="..." rounds="N">
  <research>...</research>
  <rounds>
    <round n="1">
      <implementation>...</implementation>
      <review>...</review>
    </round>
    ...
  </rounds>
</firepass_trio>
```

## Status values

- `approved`: Review passed with no blocking issues, or all issues were fixed within the review rounds
- `needs_fixes`: Reviewer marked NEEDS-FIXES but max review rounds were exhausted
- `research_failed`: The researcher step did not complete successfully
- `implementation_failed`: The worker step did not complete successfully
- `review_failed`: The reviewer step did not complete successfully

## Security

Researcher and reviewer are read-only; worker has full access including bash. All I/O is sandboxed to the provided `cwd`. See the per-role tool lists in `skills/firepass-researcher/SKILL.md`, `skills/firepass-worker/SKILL.md`, and `skills/firepass-reviewer/SKILL.md`.
