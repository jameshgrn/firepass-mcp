---
name: refactoring-discipline
title: Refactoring Discipline
summary: Constrain refactoring to the task's logical change; note unrelated smells instead of fixing them.
applies_to: [refactor, rename, extract, cleanup, convert, move, split, reduce, restructure]
priority: must
---
## When
- renaming, extracting, or restructuring code during a task
- noticing code smells in files you are already editing

## Do
- only refactor code directly involved in the task's logical change
- note unrelated smells in the done summary for the governor
- keep variable/function renames scoped to identifiers your task introduced or modified

## Do Not
- rename variables or functions unrelated to the task
- extract helpers for code the task did not change
- "improve" error messages, docstrings, or formatting in unrelated code paths

## Verify
- git_diff should show only hunks related to the task summary
- if unrelated hunks appear, revert them before calling done

## Escalate
- if fixing the smell is required for correctness of your task (explain why in done summary)
