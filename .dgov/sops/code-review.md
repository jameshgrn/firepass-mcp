---
name: code-review
title: Code Review Standards
summary: What to look for and how to structure findings when reviewing code changes.
applies_to: [review, reviewer, diff, audit, inspect, finding]
priority: must
---
## When
- reviewing any code change as a reviewer role
- evaluating a diff for semantic correctness

## Do
- reason backward from the implementation, not from the spec or task prompt
- flag logic errors, no-ops, silently wrong behavior, and missing edge cases
- check if the code is simple enough to debug (if writing it was hard, debugging it will be harder)
- flag behavior that would surprise the next reader of this code
- flag changes to public interfaces that break implicit contracts downstream
- note coupling chains that reach through multiple objects to get data
- structure findings by severity: blocking issues first, then suggestions

## Do Not
- flag style preferences (naming, formatting, comment quality)
- suggest architectural rewrites beyond the scope of the diff
- flag pre-existing issues that the diff did not introduce
- demand missing tests (that is a separate concern)

## Verify
- read surrounding code for context before flagging something as wrong
- confirm a flagged pattern is actually new in this diff, not pre-existing

## Escalate
- if the change reveals systemic issues beyond the diff scope
- if the diff touches a public API in a way that could break unknown consumers
