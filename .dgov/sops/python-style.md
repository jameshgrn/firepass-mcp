---
name: python-style
title: Python Toolchain & Code Style
summary: Python toolchain defaults and code-style rules for focused, explicit changes.
applies_to: [python, lint, format, style, ruff, naming, convention]
priority: must
---
## When
- editing Python source or tests
- choosing lint, format, or type-check commands
- touching functions or stateful code that could drift toward cleverness or flag piles

## Do
- prefer explicit, readable logic over dense cleverness
- keep semantic functions pure and isolate orchestration side effects

## Do Not
- leave commented-out code behind
- add docstrings, comments, or type annotations to code you did not change
- add premature abstractions before the pattern is repeated enough to justify them
- accumulate boolean flags or mutate input objects while also returning the same reference

## Verify
- fix every warning from lint, type checks, and targeted tests
- run the narrowest relevant Python verification commands for the files you changed
- review the final diff for unintended rewrites or formatting churn

## Escalate
- if the requested fix requires a broader refactor than the claimed files allow
- if repo toolchain commands in `project.toml` do not match the actual project
