---
name: testing
title: Testing Conventions
summary: Testing rules for targeted execution, scope discipline, and behavior-first verification.
applies_to: [tests, test, pytest, verification, coverage, assert, unit]
priority: must
---
## When
- writing or editing tests
- choosing verification commands after a code change
- investigating test failures during worker verification

## Do
- use `pytest -q`
- run targeted subsets with markers like `-m unit` or `-m integration`
- test behavior and edge cases, not just implementation details
- mock boundaries only: network, filesystem, and external services
- confirm a relevant test would fail before claiming the fix is real

## Do Not
- run the full test suite
- mock logic or internal state just to force a passing result

## Verify
- rerun the exact targeted command that covers the changed behavior
- confirm scope violations are avoided before settlement

## Escalate
- if the right test file is outside the current claim
- if any verification command points to an unclaimed file, report the exact
  command and path instead of widening scope ad hoc
- if the change needs broader integration coverage than the task currently allows
