---
name: error-handling
title: Error Handling & Philosophy
summary: Error handling rules for failing fast, preserving context, and avoiding silent failure.
applies_to: [errors, error, exceptions, failure, raise, catch, fallback, validation]
priority: must
---
## When
- adding or changing exception handling
- introducing new failure paths or validation checks
- touching code that currently swallows errors or returns ambiguous failure values

## Do
- fail fast with clear, actionable messages
- include operation, input context, and a suggested fix when reporting errors
- remove obsolete branches or shims instead of layering deprecation clutter
- keep control flow explicit when handling failures

## Do Not
- swallow exceptions silently
- add speculative features or extra flags while touching failure paths
- hide important failure details behind generic error text

## Verify
- exercise at least one error path, not just the happy path
- confirm failure messages still identify what operation failed and why
- confirm exceptions propagate to the right boundary instead of disappearing

## Escalate
- if the right failure contract is unclear at the interface or architecture level
- if a fix would require changing cross-module error semantics
