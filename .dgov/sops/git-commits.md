---
name: git-commits
title: Git & Commit Standards
summary: Commit and git hygiene rules for scoped work, safe history, and no secret leakage.
applies_to: [git, commit, history, merge, rebase, branch]
priority: must
---
## When
- preparing or reviewing git changes
- writing commit messages
- deciding whether to stage or commit work in a task

## Do
- keep each commit to one logical change
- use imperative mood in commit subjects
- keep commit subjects at or under 72 characters
- prefer commit messages that explain why the change exists

## Do Not
- push directly to `main`
- stage or commit unless explicitly requested by the task or workflow
- commit secrets, API keys, or `.env` files

## Verify
- inspect the diff before any commit step
- confirm the commit scope matches one logical change
- confirm no secrets or unrelated files are staged

## Escalate
- if the requested change obviously spans multiple logical commits
- if the task asks for history rewriting or other destructive git operations
